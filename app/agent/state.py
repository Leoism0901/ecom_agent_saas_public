"""
Agent 全局状态定义模块

本模块定义 LangGraph 多节点工作流中共享的 AgentState 状态容器，
是 Agent 编排层（Router → Agent → Tools）的上下文通信基础。

设计原则：
- 采用 TypedDict 而非 Pydantic Model，确保与 LangGraph 原生状态序列化机制兼容
- 消息列表采用 Annotated + add_messages 机制，支持增量追加而非全量覆盖
- 租户上下文（tenant_id / tenant_prompt）伴随整个工作流生命周期，实现多租户数据隔离
"""

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """
    LangGraph 工作流全局共享状态

    本状态对象在 Agent 执行图的每个节点之间流转，各节点按需读取 / 写入字段。
    三条属性的语义说明如下。
    """

    messages: Annotated[list[AnyMessage], add_messages]
    """
    会话上下文消息队列

    用于存放和追加整个会话的上下文消息，涵盖 System、Human、AI、Tool 等所有角色。
    采用 LangGraph 的 add_messages 机制自动合并 —— 节点返回的新消息会追加到列表末尾，
    而非覆盖整个 messages 字段，保证多轮对话上下文的完整性。
    """

    tenant_id: str
    """
    当前请求所属的租户（店铺）ID

    贯穿整个工作流的数据隔离标识，来源为 Router 层从 X-Tenant-ID 请求头提取，
    随后由各节点透传至 Service 层（如向量检索、FAQ 缓存、Mock 工具调用），
    确保每个租户只能访问自己名下的数据。
    """

    tenant_prompt: str
    """
    当前租户专属的系统提示词

    包含租户级别的定制人设、业务规则、品牌语调等约束信息，
    由 PromptManager 按需注入到 LLM 调用节点中，动态约束大模型的输出风格与业务边界，
    实现「千人千面」的 Agent 回复能力。
    """

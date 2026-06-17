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
    每条属性附带完整语义注释，说明来源、用途、流转链路与业务约束。
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
    由 ChatService.get_tenant_prompt() 三层级联加载（Redis → MySQL → 兜底），
    在 LLM 调用节点中作为 role="system" 强制注入请求体首位，
    动态约束大模型的输出风格与业务边界，实现「千人千面」的 Agent 回复能力。
    """

    chat_history: list[dict]
    """
    从 Redis List 中读取的短期对话记忆（近 5 轮历史，最多 10 条消息）

    数据来源：app.utils.redis_memory.get_memory_messages() 的返回值，
    在 Agent 工作流启动前（Router 层或 graph.run_agent 入口处）注入初始状态。

    每条消息格式为：
        {
            "role": "user" | "assistant" | "tool",
            "content": "消息文本...",
            "timestamp": "2026-06-16T10:30:00+00:00",
            "metadata": {...}   # 可选，仅写入时传了 metadata 才存在
        }

    与 messages 字段的区别：
    - messages：LangChain 消息对象列表（HumanMessage / AIMessage / ToolMessage），
      供 LangGraph 内部节点流转和 LLM 调用使用。
    - chat_history：纯 Python dict 列表，来自 Redis 短期记忆存储，
      用于在 LLM 节点的 Prompt 中注入历史上下文摘要，或在多轮对话开始时
      预热 messages 列表（将 dict 转换为 LangChain 消息对象后追加到 messages）。
    """

    question: str
    """
    当前轮次的买家原始问题文本

    由 run_agent() 在组装初始 State 时注入，对应 ChatService.process_chat()
    接收的 question 参数。LLM 节点在构建请求体 messages 数组时，
    将本条消息作为最后一条 role="user" 消息追加到列表末尾。
    """

    session_id: str
    """
    当前会话的唯一标识（UUID 字符串）

    来源为 Router 层传入或后端自动生成的会话 UUID，贯穿整个 Agent 工作流。
    在长记忆压缩节点中用于定位 ChatLog 记录，以便将提炼出的结构化摘要
    （summary / tags / emotion）持久化写入对应 session 的 metadata_json 字段。

    与 chat_history 的区别：
    - session_id：数据库 ChatLog 记录的会话关联键，用于持久化写入。
    - chat_history：Redis 短期记忆中的历史消息快照，用于 LLM 上下文注入。
    """

    summary: str
    """
    长记忆压缩摘要字段

    用于存放压缩后的会话摘要文本。当 state["messages"] 列表长度
    超过阈值（≥ 11 条）时，由压缩节点将历史消息压缩为一段精炼的
    摘要文本存入本字段，供后续 LLM 调用时作为上下文前缀注入，
    实现「长记忆压缩」机制，在控制 Token 消耗的同时保留关键对话信息。

    当前阶段（压缩节点已实现）：
    - 本字段初始值为空字符串 ""，由 run_agent() 在组装初始 State 时注入。
    - 压缩节点 summarize_memory 将提炼出的结构化 JSON 回写到本字段，
      并持久化到 MySQL ChatLog.metadata_json 中。
    """

    is_human_needed: bool
    """
    人工接管触发标志位

    当敏感词前置拦截（should_escalate_to_human 条件路由）检测到买家消息中
    包含「诈骗」「投诉」「12315」「律师函」「工商」「维权」「消协」「法院」
    等极端意图关键词时，由条件路由函数将其设为 True，并将图流转定向至
    human_fallback_node，绕过 LLM 调用直接走人工流程。

    - 默认值：False（正常流转，无需人工介入）。
    - 设为 True 后，human_fallback_node 读取本标志位并生成工单数据写入 ticket_data。
    """

    ticket_data: dict
    """
    人工工单数据容器

    当 is_human_needed 被触发后，由 human_fallback_node 生成的结构化工单信息，
    包含但不限于：触发关键词、买家原始消息、会话 ID、租户 ID、时间戳等字段。

    格式示例：
        {
            "trigger_keyword": "12315",
            "user_message": "我要打12315投诉你们",
            "session_id": "uuid-xxx",
            "tenant_id": "888",
            "created_at": "2026-06-17T10:30:00+08:00"
        }

    - 默认值：空字典 {}，由 run_agent() 在组装初始 State 时注入。
    - 后续阶段可将本字段的数据写入 MySQL 工单表或推送到客服工作台。
    """
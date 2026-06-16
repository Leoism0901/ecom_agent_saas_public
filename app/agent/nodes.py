"""
LangGraph 工作流节点定义模块

本模块定义 Agent 编排图中各个节点的函数签名与实现。
当前阶段已从"空壳测试"升级为"LLM 真实调用"阶段 ——
所有核心 Prompt 组装与 LLM 调用逻辑位于 app.services.chat_service，
本模块作为 LangGraph 节点的入口适配层，负责：
1. 从 chat_service 导入核心节点实现（llm_generate_node）。
2. 提供意图分类、知识检索等后续节点的注册占位。

节点在业务链路中的顺序（当前阶段 —— 单 LLM 节点直连）：
    START → llm_generate_node → END

后续阶段将扩展为多节点条件拓扑：
    START → intent_classifier ─┬─ logistics → llm_generate_node ─→ END
                                ├─ product   → rag_retrieval  ──→ llm_generate_node → END
                                └─ chitchat  → llm_generate_node ─→ END

架构红线（遵循 CLAUDE.md）：
- 本模块禁止直接调用大模型 API 或组装 Prompt，所有逻辑下沉至 Service 层。
- 节点函数签名严格适配 LangGraph StateGraph.add_node() 规范。
"""

import logging

from app.agent.state import AgentState
from app.services.chat_service import llm_generate_node  # noqa: F401

# ============================================================
# 模块级日志记录器
# ============================================================
_logger = logging.getLogger(__name__)


async def intent_classifier_node(state: AgentState) -> dict:
    """
    意图分类节点 —— 未来阶段实现。

    未来业务链路中的角色：
    1. 调用大模型进行意图识别，将买家消息分类到预设意图枚举中。
    2. 将分类结果写入 state，供条件边（conditional edge）分流。

    当前阶段：直接委托到 llm_generate_node，由大模型一次性完成
    意图理解 + 工具调用 + 回复生成的全流程。

    Args:
        state: LangGraph 全局共享状态。

    Returns:
        dict: 包含 messages 更新的字典。
    """
    pass


async def check_logistics_node(state: AgentState) -> dict:
    """
    查物流节点 —— 未来阶段实现（独立物流查询节点）。

    当前阶段委托到 llm_generate_node 统一处理。

    Args:
        state: LangGraph 全局共享状态。

    Returns:
        dict: 包含 messages 更新的字典。
    """
    pass


async def rag_retrieval_node(state: AgentState) -> dict:
    """
    RAG 知识库检索节点 —— 未来阶段实现（向量检索节点）。

    当前阶段委托到 llm_generate_node 统一处理。

    Args:
        state: LangGraph 全局共享状态。

    Returns:
        dict: 包含 messages 更新的字典。
    """
    pass
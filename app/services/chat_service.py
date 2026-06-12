"""
会话日志（ChatLog）业务服务层

本模块是电商 SaaS 平台的多租户会话日志核心，负责：
1. 会话日志的创建与历史查询，所有写入路径强制绑定 tenant_id
2. 所有数据操作均为纯异步（async def），绝不导入 FastAPI 路由模块
3. 从底层 SQL 语句阻断跨租户越权访问 —— 每条查询/写入必须显式过滤 tenant_id

多租户隔离策略（架构红线，严禁违反）：
- 写入路径：ChatLogCreate 不含 tenant_id（前端不可信），tenant_id 由 Router 从 Header 提取
  后传入本层，Service 负责将其显式写入 ORM 实例的 tenant_id 字段
- 查询路径：每条 SELECT 语句的 WHERE 子句必须包含 ChatLog.tenant_id == tenant_id，
  即使 Router 传入的 tenant_id 是伪造的，SQL 层面也会强制过滤，防止租户 A 读到租户 B 的数据

架构约束（遵循 .claudecoderc 与 CLAUDE.md）：
- 本层是 Router → Database 之间的唯一数据通道
- 所有函数第一个参数为 db: AsyncSession，配合 FastAPI Depends(get_db) 依赖注入
- 字段映射在 Service 层完成：API 字段 bot_reply → ORM 列 ai_response，intent → metadata_json
- session_id 由后端自动生成 UUID，前端不可指定，防止会话伪造
"""

import uuid
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChatLog
from app.schemas import ChatLogCreate


async def create_chat_log(
    db: AsyncSession,
    tenant_id: int,
    log_data: ChatLogCreate,
) -> ChatLog:
    """
    创建会话日志 —— 强制绑定租户 ID，从源头阻断跨租户越权写入

    核心安全逻辑：
    1. tenant_id 不由前端传入，而是由 Router 从 HTTP Header (X-Tenant-ID) 提取后注入
    2. 本函数显式将 tenant_id 写入 ORM 实例，前端任何伪造 tenant_id 的尝试均无效
    3. session_id 由后端 UUID 生成，防止前端伪造会话链

    字段映射说明：
    - log_data.bot_reply（API 字段）→ ORM 列 ai_response
    - log_data.intent（API 字段）  → ORM 列 metadata_json 内嵌键 "intent"

    Args:
        db:        由 FastAPI get_db() 依赖注入的异步数据库会话
        tenant_id: 租户唯一标识，由 Router 从 X-Tenant-ID Header 提取后传入
        log_data:  Pydantic 校验后的会话日志创建请求体（不含 tenant_id / session_id）

    Returns:
        已持久化并回填自增 ID 的 ChatLog ORM 实例
    """
    # ---------------------------------------------------------
    # 将 intent 意图分类标签存入 metadata_json 扩展字段
    # 设计意图：intent 是业务层高频变动字段，存入 JSON 列可避免频繁 DDL ALTER TABLE
    # ---------------------------------------------------------
    metadata_payload: dict = {}
    if log_data.intent:
        metadata_payload["intent"] = log_data.intent

    # ---------------------------------------------------------
    # 构建 ORM 实例，显式写入 tenant_id（多租户安全的核心保障点）
    # ---------------------------------------------------------
    chat_log = ChatLog(
        # 强制绑定租户 —— 前端不可信，此值唯一来源为 X-Tenant-ID Header
        tenant_id=tenant_id,
        # 后端自动生成会话 UUID，前端无法伪造
        session_id=str(uuid.uuid4()),
        # 买家原始消息
        user_message=log_data.user_message,
        # API 字段 bot_reply → ORM 列 ai_response 映射
        ai_response=log_data.bot_reply,
        # 扩展字段：当前阶段暂存 intent 意图标签，后续扩展情绪标签、状态快照等
        metadata_json=metadata_payload if metadata_payload else None,
    )

    db.add(chat_log)
    await db.commit()
    # refresh 触发数据库端默认值回填（自增 ID、created_at 等）
    await db.refresh(chat_log)

    return chat_log


async def get_chat_logs(
    db: AsyncSession,
    tenant_id: int,
    limit: int = 50,
) -> Sequence[ChatLog]:
    """
    查询指定租户的会话日志历史 —— 强制过滤 tenant_id，阻断跨租户越权读取

    安全核心：WHERE 子句绝对包含 ChatLog.tenant_id == tenant_id，从 SQL 层面确保：
    - 租户 A 即使猜测到租户 B 的 session_id，也无法读取到租户 B 的对话数据
    - 多租户数据隔离的最后一道防线，不依赖前端或 Router 层的过滤逻辑

    排序规则：按消息创建时间倒序（最新对话排在最前），便于前端展示最近会话列表

    Args:
        db:        由 get_db() 注入的异步数据库会话
        tenant_id: 租户唯一标识，由 Router 从 X-Tenant-ID Header 提取后传入
        limit:     返回的最大记录数（默认 50，防止一次返回过多数据）

    Returns:
        ChatLog ORM 实例序列（仅包含当前租户的数据，可能为空序列）
    """
    result = await db.execute(
        select(ChatLog)
        # 【多租户强隔离红线】WHERE 条件必须显式过滤 tenant_id
        .where(ChatLog.tenant_id == tenant_id)
        # 按创建时间倒序：最新消息在最前
        .order_by(ChatLog.created_at.desc())
        # 限制返回数量，防止单次查询返回海量数据拖垮接口
        .limit(limit)
    )
    return result.scalars().all()


async def get_chat_log_by_id(
    db: AsyncSession,
    log_id: int,
    tenant_id: int,
) -> ChatLog | None:
    """
    按日志 ID 和租户 ID 双重条件查询单条会话日志

    与 get_chat_logs 不同，本函数在 WHERE 子句中同时过滤 id 和 tenant_id，
    确保即使是按主键精确查询，也不会泄露跨租户数据。

    使用场景：Router 层需要获取单条日志详情以回填 AI 异步响应时。

    Args:
        db:        由 get_db() 注入的异步数据库会话
        log_id:    会话日志自增主键 ID
        tenant_id: 租户唯一标识（双重校验，防止主键枚举攻击）

    Returns:
        ChatLog ORM 实例（存在且归属当前租户时）或 None（不存在或不属于当前租户）
    """
    result = await db.execute(
        select(ChatLog)
        # 【多租户强隔离红线】同时过滤 id 和 tenant_id，双重保险
        .where(ChatLog.id == log_id)
        .where(ChatLog.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()

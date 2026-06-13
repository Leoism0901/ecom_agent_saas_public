"""
会话日志（ChatLog）业务服务层

本模块是电商 SaaS 平台的多租户会话日志核心，负责：
1. 会话日志的创建与历史查询，所有写入路径强制绑定 tenant_id
2. 处理买家消息的完整业务链路：Redis FAQ 缓存拦截 → 持久化 → LLM 调用（待接入）
3. 所有数据操作均为纯异步（async def），绝不导入 FastAPI 路由模块
4. 从底层 SQL 语句阻断跨租户越权访问 —— 每条查询/写入必须显式过滤 tenant_id

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
- Redis FAQ 缓存命中时，必须立即返回预制回复，彻底跳过后续 LLM 调用链路
- 所有 Redis 连接配置必须从 .env 环境变量读取，严禁硬编码敏感信息
"""

import os
import uuid
from typing import Optional, Sequence

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import redis.asyncio as aioredis

from app.models import ChatLog
from app.schemas import ChatLogCreate

# ============================================================
# 环境变量加载（确保模块被独立 import 时也能读到 .env 配置）
# 重复调用 load_dotenv() 是无害的 —— 后续调用自动跳过
# ============================================================
load_dotenv()

# ============================================================
# Redis 连接配置 —— 全部从 .env 环境变量读取（绝对禁止硬编码）
# ============================================================
_REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
_REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
_REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "")
_REDIS_DB: int = int(os.getenv("REDIS_DB", "0"))

# ============================================================
# Redis 异步连接池（模块级单例，惰性初始化）
# 使用连接池而非每次请求创建新连接，避免 TCP 握手开销
# ConnectionPool 在首次调用 _get_redis_client() 时才创建
# ============================================================
_redis_pool: Optional[aioredis.ConnectionPool] = None


def _get_redis_pool() -> aioredis.ConnectionPool:
    """
    获取模块级 Redis 异步连接池（惰性初始化，全局复用）。

    只在首次调用时创建连接池实例，后续调用返回同一个池对象。
    连接池负责管理 TCP 连接的复用与回收，避免每次请求都重新建立连接。

    Returns:
        aioredis.ConnectionPool: 已配置的异步 Redis 连接池
    """
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.ConnectionPool(
            host=_REDIS_HOST,
            port=_REDIS_PORT,
            password=_REDIS_PASSWORD if _REDIS_PASSWORD else None,
            db=_REDIS_DB,
            socket_connect_timeout=3,   # 连接超时 3 秒（缓存场景应快速失败）
            socket_timeout=3,           # 读写超时 3 秒
            decode_responses=True,      # 自动 bytes → str 解码
            max_connections=20,         # 连接池上限（与 DB_POOL_SIZE 平级）
        )
    return _redis_pool


async def _lookup_faq_cache(user_message: str) -> Optional[str]:
    """
    查询 Redis FAQ 缓存，尝试匹配买家消息对应的预制回复。

    匹配策略（由严到松）：
    1. 清洗用户输入，去除首尾空白字符
    2. 使用 SCAN 迭代获取 Redis 中所有 faq:* 键（避免 KEYS 阻塞主线程）
    3. 对每条 FAQ 键去掉 faq: 前缀得到问题文本
    4. 子串匹配：若用户消息包含 FAQ 问题文本，或 FAQ 问题文本包含用户消息，即视为命中
    5. 命中后立即返回预制回复文本，未命中返回 None

    降级策略：
    - 如果 Redis 不可达或发生任何异常，静默返回 None（缓存不可用不影响主流程）
    - 调用方收到 None 后应继续走 LLM 生成路径

    Args:
        user_message: 买家原始消息文本（前端输入，可能含空白字符）

    Returns:
        匹配到的 FAQ 预制回复文本（命中时），None（未命中或 Redis 不可用时）
    """
    try:
        # 从连接池获取一个异步 Redis 客户端实例
        r = aioredis.Redis(connection_pool=_get_redis_pool())

        # 清洗用户输入：去除首尾空白，便于子串匹配
        cleaned_msg = user_message.strip()
        if not cleaned_msg:
            return None  # 空消息不匹配任何 FAQ

        # ---------------------------------------------------------
        # 使用 SCAN 迭代获取所有 faq:* 键
        # 为什么不直接用 KEYS：
        #   KEYS 会阻塞 Redis 主线程，在 FAQ 条目多的场景下可能影响其他请求。
        #   SCAN 是游标迭代，每次只扫描少量 Key 后立即交还 CPU，对线上服务无感。
        #   虽然当前 FAQ 只有 3 条，但建立正确的编码习惯从第一天开始。
        # ---------------------------------------------------------
        faq_keys: list[str] = []
        cursor = 0
        while True:
            cursor, keys = await r.scan(cursor=cursor, match="faq:*", count=50)
            if keys:
                faq_keys.extend(keys)
            if cursor == 0:
                break

        if not faq_keys:
            return None  # Redis 中不存在任何 FAQ 缓存

        # ---------------------------------------------------------
        # 子串匹配：遍历所有 FAQ 键，检查是否与用户消息相互包含
        # 示例：
        #   用户："你们发什么快递啊"  → FAQ 键 "faq:发什么快递" → 命中（包含）
        #   用户："发什么快递"        → FAQ 键 "faq:发什么快递" → 命中（包含）
        #   用户："退换货"           → FAQ 键 "faq:退换货规则" → 命中（FAQ键包含用户输入）
        # ---------------------------------------------------------
        FAQ_PREFIX_LEN = 4  # len("faq:") == 4

        for key in faq_keys:
            faq_question = key[FAQ_PREFIX_LEN:]  # 去掉 faq: 前缀得到纯问题文本
            if faq_question in cleaned_msg or cleaned_msg in faq_question:
                # 命中：从 Redis 读取预制回复值
                cached_reply = await r.get(key)
                if cached_reply:
                    return cached_reply  # 🎯 缓存命中，直接返回，彻底截断后续 LLM 调用

        # 遍历完所有 FAQ 键均未命中
        return None

    except Exception:
        # Redis 不可用时静默降级，返回 None 让调用方继续走 LLM 路径
        # 不上抛异常 —— FAQ 缓存是加速手段，不是关键路径，不应阻断主流程
        return None


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


# ============================================================
# 买家消息处理主入口 —— process_chat_message
# 完整业务链路：FAQ 缓存拦截 → 持久化 →（待接入）LLM 生成
# ============================================================


async def process_chat_message(
    db: AsyncSession,
    tenant_id: int,
    user_message: str,
    session_id: Optional[str] = None,
) -> ChatLog:
    """
    处理买家消息的核心业务函数 —— FAQ 缓存优先 + 持久化 + LLM 待接入。

    执行流（三层级联）：
    ┌─────────────────────────────────────────────────────────────┐
    │ 第一层：【Redis FAQ 缓存拦截】                              │
    │   查询 Redis 中 faq:* 键，若命中预制回复 → 直接 return      │
    │   命中后彻底跳过后续所有步骤，绝不调用大模型                 │
    │                                                             │
    │ 第二层：【数据持久化】                                       │
    │   将买家消息与回复写入 sys_chat_log，session_id 后端自动生成 │
    │   metadata_json 记录来源标识（faq_cache / llm_pending）      │
    │                                                             │
    │ 第三层：【LLM Agent 调用】（后续阶段接入）                   │
    │   缓存未命中时调用 LangGraph 编排的 AI 售后 Agent 生成回复    │
    │   Agent 回复通过异步回调回填到 sys_chat_log.ai_response      │
    └─────────────────────────────────────────────────────────────┘

    多租户安全：
    - tenant_id 由 Router 从 X-Tenant-ID Header 提取后传入
    - Service 层负责显式写入 ChatLog.tenant_id，前端无法伪造

    Args:
        db:          由 FastAPI get_db() 依赖注入的异步数据库会话
        tenant_id:   租户唯一标识，来自 X-Tenant-ID Header（Router 层提取）
        user_message:买家原始消息文本
        session_id:  可选——会话 UUID，用于多轮对话关联；不传则自动生成新会话

    Returns:
        ChatLog ORM 实例（已持久化），ai_response 可能已填充（缓存命中）或为空（待 LLM）
    """
    # ================================================================
    # 第一层：Redis FAQ 缓存拦截
    # 在数据库写入和 LLM 调用之前优先查缓存，命中即走快速返回通道
    # ================================================================
    cached_reply: Optional[str] = await _lookup_faq_cache(user_message)

    if cached_reply is not None:
        # ---------------------------------------------------------
        # 🎯 缓存命中路径（快速通道）
        # 将预制 FAQ 回复直接作为 ai_response，标记来源为 faq_cache
        # 彻底跳过 LLM Agent 调用，减少 Token 消耗与响应延迟
        # ---------------------------------------------------------
        chat_log = ChatLog(
            tenant_id=tenant_id,
            session_id=session_id or str(uuid.uuid4()),
            user_message=user_message,
            ai_response=cached_reply,  # FAQ 预制回复，直接填充
            metadata_json={
                "source": "faq_cache",  # 标识回复来源，便于后续数据统计与分析
                "cached": True,
            },
        )
        db.add(chat_log)
        await db.commit()
        await db.refresh(chat_log)
        return chat_log

    # ================================================================
    # 第二层：缓存未命中路径 —— 持久化 + 留空 LLM 待填充
    # ai_response 设为 None，metadata_json 标记来源为 llm_pending
    # 后续阶段接入 LangGraph Agent 后，由异步回调填充 ai_response
    # ================================================================
    chat_log = ChatLog(
        tenant_id=tenant_id,
        session_id=session_id or str(uuid.uuid4()),
        user_message=user_message,
        ai_response=None,  # 待 LLM Agent 异步生成后回填
        metadata_json={
            "source": "llm_pending",  # 标识此条日志尚未经过 AI 处理
            "cached": False,
        },
    )
    db.add(chat_log)
    await db.commit()
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

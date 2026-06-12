"""
会话日志（ChatLog）接口路由层

本模块是电商 SaaS 平台的多租户会话日志 REST API 入口，负责：
1. 强制从 HTTP Header (X-Tenant-ID) 提取租户标识，实现请求级多租户隔离
2. 接收 Pydantic Schema 校验请求体，透明透传至 Service 层
3. 将 Service 返回的 ORM 模型转换为 Pydantic Response 序列化输出

架构约束（遵循 .claudecoderc 与 CLAUDE.md）：
- 极薄路由原则：本层仅做「参数提取 → Service 透传 → Response 序列化」三步
- 严禁在 Router 中写数据库 WHERE 过滤、字段映射、业务判断等逻辑
- 所有数据库会话通过 Depends(get_db) 依赖注入，严禁直接操作引擎
- 多租户隔离的最后一道防线在 Service 层的 SQL WHERE 子句，Router 只负责传递 tenant_id
"""

from typing import List

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import ChatLogCreate, ChatLogResponse
from app.services.chat_service import create_chat_log, get_chat_logs

# ---------------------------------------------------------
# 路由实例
# ---------------------------------------------------------
router = APIRouter(prefix="/api/v1/chats", tags=["Chat Logs"])


# ============================================================
# 辅助函数：Header 租户 ID 提取与校验
# ============================================================

def _parse_tenant_id(raw: str) -> int:
    """
    将 HTTP Header 中的 X-Tenant-ID 字符串转为 int

    Router 层作为 HTTP 边界，负责将 Header 字符串值转换为 Service 层期望的 int 类型。
    如果转换失败（非数字字符串），返回 400 而非 500，给前端清晰的错误提示。

    Args:
        raw: X-Tenant-ID Header 原始字符串值

    Returns:
        int 类型的租户 ID

    Raises:
        HTTPException(400): Header 值不是合法的整数
    """
    try:
        return int(raw.strip())
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=400,
            detail=f"X-Tenant-ID 必须为有效的整数，当前值：'{raw}'",
        )


# ============================================================
# POST /api/v1/chats  —— 创建会话日志
# ============================================================

@router.post("/", response_model=ChatLogResponse, status_code=201)
async def create_chat_log_endpoint(
    log_data: ChatLogCreate,
    x_tenant_id: str = Header(
        ...,
        description="租户唯一标识（必填），从 X-Tenant-ID Header 中提取，前端不可伪造",
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    创建一条新的买家-AI 会话日志

    请求流程：
    1. FastAPI 自动从 Header 提取 X-Tenant-ID → 字符串
    2. Router 将其转为 int → 透明透传至 Service
    3. Service 显式写入 ChatLog.tenant_id（前端不可信，此值唯一来源为 Header）
    4. Service 返回 ORM 实例 → Router 转换为 ChatLogResponse 序列化返回

    Args:
        log_data:     Pydantic 校验后的会话日志请求体（不含 tenant_id / session_id）
        x_tenant_id:  从 HTTP Header 强制提取的租户 ID（Swagger 自动渲染为必填输入框）
        db:           get_db() 注入的异步数据库会话

    Returns:
        ChatLogResponse: 包含自增 ID、session_id、时间戳等完整字段的响应体
    """
    # Header 字符串 → int 类型转换（HTTP 边界层的职责）
    tenant_id_int = _parse_tenant_id(x_tenant_id)

    # 透明透传至 Service 层 —— 所有业务逻辑（tenant_id 绑定、UUID 生成、字段映射）下沉在 Service
    chat_log_orm = await create_chat_log(
        db=db,
        tenant_id=tenant_id_int,
        log_data=log_data,
    )

    # ORM 实例 → Pydantic Response 序列化（from_attributes=True 自动映射）
    return ChatLogResponse.model_validate(chat_log_orm)


# ============================================================
# GET /api/v1/chats  —— 查询当前租户的全部会话日志
# ============================================================

@router.get("/", response_model=List[ChatLogResponse])
async def list_chat_logs(
    x_tenant_id: str = Header(
        ...,
        description="租户唯一标识（必填），仅返回该租户的会话日志，实现多租户数据隔离",
    ),
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """
    查询当前租户的会话日志历史

    多租户隔离机制（纵深防御）：
    - 第一层：Router 从 Header 提取 tenant_id，前端无法在 Body 中伪造
    - 第二层：Service 层 WHERE ChatLog.tenant_id == tenant_id，SQL 级强制过滤
    - 结果：即使两个租户同时调用此接口，各自只能看到自己的数据

    Args:
        x_tenant_id: 从 HTTP Header 提取的租户 ID（Swagger 自动渲染为必填输入框）
        limit:       返回的最大记录数（默认 50）
        db:          get_db() 注入的异步数据库会话

    Returns:
        List[ChatLogResponse]: 仅包含当前租户的会话日志列表（按时间倒序）
    """
    tenant_id_int = _parse_tenant_id(x_tenant_id)

    # 透明透传 —— WHERE tenant_id 过滤完全由 Service 层负责
    chat_logs_orm = await get_chat_logs(
        db=db,
        tenant_id=tenant_id_int,
        limit=limit,
    )

    # 批量转换 ORM → Pydantic Response
    return [ChatLogResponse.model_validate(log) for log in chat_logs_orm]

"""
租户（Tenant）接口路由层

本模块是电商 SaaS 平台的租户管理 REST API 入口，负责：
1. 接收并校验租户创建/查询的 HTTP 请求参数
2. 透明透传至 tenant_service 层执行业务操作
3. 将 Service 返回的 ORM 模型转换为 TenantResponse 序列化输出

架构约束（遵循 .claudecoderc 与 CLAUDE.md）：
- 极薄路由原则：仅做「参数提取 → Service 透传 → Response 序列化」
- 严禁在 Router 中直接操作数据库或包含业务逻辑
- 所有数据库会话通过 Depends(get_db) 依赖注入
- 租户管理属于平台运维接口，后续应接入 JWT 权限校验
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import TenantCreate, TenantResponse
from app.services.tenant_service import create_tenant, get_tenant_by_id

# ---------------------------------------------------------
# 路由实例
# ---------------------------------------------------------
router = APIRouter(prefix="/api/v1/tenants", tags=["Tenants"])


# ============================================================
# POST /api/v1/tenants  —— 创建新租户
# ============================================================

@router.post("/", response_model=TenantResponse, status_code=201)
async def create_tenant_endpoint(
    tenant_data: TenantCreate,
    db: AsyncSession = Depends(get_db),
):
    """
    创建新租户（店铺/商户入驻）

    说明：
    - 本接口为平台管理接口，不要求 X-Tenant-ID Header（创建租户时尚无租户身份）
    - 租户 ID 由 MySQL 自增主键自动生成，tenant_name 全局唯一
    - 套餐档位通过 plan_type 字段指定，初始 metadata 为空对象

    Args:
        tenant_data: Pydantic 校验后的租户创建请求体
        db:          get_db() 注入的异步数据库会话

    Returns:
        TenantResponse: 包含自增 tenant_id、时间戳等完整字段的响应体
    """
    # 透明透传 —— 字段映射（plan_type → package_level）完全由 Service 层负责
    tenant_orm = await create_tenant(db=db, tenant_data=tenant_data)

    # ORM 实例 → Pydantic Response 序列化（from_attributes=True + validation_alias 自动完成字段转换）
    return TenantResponse.model_validate(tenant_orm)


# ============================================================
# GET /api/v1/tenants/{tenant_id}  —— 查询单个租户详情
# ============================================================

@router.get("/{tenant_id}", response_model=TenantResponse)
async def get_tenant_endpoint(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    按租户 ID 查询单个租户详情

    Args:
        tenant_id: URL 路径参数 —— 租户唯一标识（数据库自增主键）
        db:        get_db() 注入的异步数据库会话

    Returns:
        TenantResponse: 租户完整信息

    Raises:
        HTTPException(404): 指定 tenant_id 的租户不存在
    """
    # URL 路径参数 str → int 类型转换（HTTP 边界层的职责）
    try:
        tenant_id_int = int(tenant_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"租户 ID 必须为有效的整数，当前值：'{tenant_id}'",
        )

    # 透明透传至 Service 层
    tenant_orm = await get_tenant_by_id(db=db, tenant_id=tenant_id_int)

    # Service 返回 None 时 → 转换为标准 404 响应
    if tenant_orm is None:
        raise HTTPException(
            status_code=404,
            detail=f"租户 ID={tenant_id_int} 不存在",
        )

    return TenantResponse.model_validate(tenant_orm)

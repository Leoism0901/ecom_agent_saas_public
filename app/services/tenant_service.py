"""
租户（Tenant）业务服务层

本模块是电商 SaaS 平台的租户管理核心，负责：
1. 租户的创建、查询、更新与状态管理（当前阶段先落地 Create + Read）
2. 所有数据操作均为纯异步（async def），绝不导入 FastAPI 路由模块
3. 为 Router 层提供透明的 ORM 模型实例，由 Router 负责转换为 Pydantic Response

架构约束（遵循 .claudecoderc 与 CLAUDE.md）：
- 本层是 Router → Database 之间的唯一数据通道，Router 严禁直接操作引擎或 Session
- 所有函数第一个参数为 db: AsyncSession，配合 FastAPI Depends(get_db) 依赖注入
- 字段映射在 Service 层完成：API 字段 plan_type → ORM 列 package_level
- 严禁硬编码敏感数据，所有配置从 .env 读取（由 database.py 统一管理）
"""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Tenant
from app.schemas import TenantCreate


async def create_tenant(
    db: AsyncSession,
    tenant_data: TenantCreate,
) -> Tenant:
    """
    创建新租户记录

    接收 Pydantic 校验后的租户创建请求，完成 API 字段 → ORM 列名的映射转换后写入数据库。
    租户 ID 由 MySQL 自增主键自动生成，创建成功后通过 refresh 回填至 ORM 实例。

    字段映射说明：
    - tenant_data.plan_type（API 字段）→ ORM 列 package_level
    - metadata_json 初始化空对象，后续通过更新接口按需填充（Logo URL、签约到期日等）

    Args:
        db:          由 FastAPI get_db() 依赖注入的异步数据库会话
        tenant_data: Pydantic 校验后的租户创建请求体（不含 ID、不含时间戳）

    Returns:
        已持久化并回填自增 ID 的 Tenant ORM 实例

    Raises:
        sqlalchemy.exc.IntegrityError: 如果 tenant_name 与已有租户重名（uk_tenant_name 唯一约束）
    """
    # API → ORM 字段映射：plan_type（前端语义）→ package_level（数据库列名）
    tenant = Tenant(
        tenant_name=tenant_data.tenant_name,
        package_level=tenant_data.plan_type,  # 套餐档位：API "plan_type" 对应 DB "package_level"
        metadata_json={},  # 初始化为空字典，后续通过 Update 接口扩展（Logo URL 等）
    )

    db.add(tenant)
    await db.commit()
    # refresh 触发数据库端默认值（自增 ID、created_at 等）回填到 ORM 实例
    await db.refresh(tenant)

    return tenant


async def get_tenant_by_id(
    db: AsyncSession,
    tenant_id: int,
) -> Optional[Tenant]:
    """
    按主键 ID 查询单个租户

    使用 SQLAlchemy 2.0 风格的 select 语句进行异步查询，返回单个 Tenant 实例。
    如果指定 ID 的租户不存在，返回 None，由 Router 层决定返回 404 或空响应。

    Args:
        db:        由 get_db() 注入的异步数据库会话
        tenant_id: 租户唯一标识（数据库自增主键，int 类型）

    Returns:
        Tenant ORM 实例（存在时）或 None（不存在时）
    """
    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id)
    )
    return result.scalar_one_or_none()


async def get_all_tenants(
    db: AsyncSession,
    limit: int = 100,
    offset: int = 0,
) -> list[Tenant]:
    """
    分页查询全部租户列表

    按入驻时间倒序排列，最新入驻的租户排在前面。
    此接口为管理后台所用，生产环境应加权限校验。

    Args:
        db:     由 get_db() 注入的异步数据库会话
        limit:  每页返回的最大记录数（默认 100）
        offset: 分页偏移量（默认 0，即首页）

    Returns:
        租户 ORM 实例列表（可能为空列表）
    """
    result = await db.execute(
        select(Tenant)
        .order_by(Tenant.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())

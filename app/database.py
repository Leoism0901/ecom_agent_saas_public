"""
数据库引擎与异步会话管理模块

本模块是电商 SaaS 平台的数据访问基础设施层，负责：
1. 创建 MySQL 异步引擎（基于 mysql+aiomysql 驱动）
2. 提供连接池配置与异步会话工厂
3. 定义 SQLAlchemy 2.0 声明式 ORM 模型基类
4. 提供 FastAPI 依赖注入函数，通过 yield 模式管理请求级会话生命周期

架构约束（遵循 .claudecoderc 与 CLAUDE.md）：
- Router 层仅通过 get_db() 获取会话，严禁直接操作引擎
- 所有数据持久化逻辑必须下沉至 Service 层
- 所有配置项统一从 .env 环境变量读取，严禁硬编码敏感信息
"""

import os
from typing import AsyncGenerator

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

# 加载项目根目录 .env 文件（所有环境变量读取操作必须在此行之后）
load_dotenv()

# ============================================================
# 数据库连接配置（全部从 .env 读取，无硬编码默认值）
# ============================================================

# MySQL 主机地址
MYSQL_HOST: str = os.getenv("MYSQL_HOST", "localhost")

# MySQL 端口
MYSQL_PORT: int = int(os.getenv("MYSQL_PORT", "3307"))

# 数据库用户名
MYSQL_USER: str = os.getenv("MYSQL_USER", "root")

# 数据库密码（敏感信息，必须通过 .env 配置）
MYSQL_PASSWORD: str = os.getenv(
    "MYSQL_PASSWORD",
    "请在.env中配置MYSQL_PASSWORD",
)

# 目标数据库名
MYSQL_DATABASE: str = os.getenv("MYSQL_DATABASE", "ecommerce_saas")

# 数据库字符集
DB_CHARSET: str = os.getenv("DB_CHARSET", "utf8mb4")

# 构建异步数据库连接 URL
# 驱动选择：mysql+aiomysql —— 基于 asyncio 的纯异步 MySQL 驱动，避免阻塞事件循环
DATABASE_URL: str = (
    f"mysql+aiomysql://{MYSQL_USER}:{MYSQL_PASSWORD}"
    f"@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}"
    f"?charset={DB_CHARSET}"
)

# ============================================================
# 连接池参数（全部从 .env 读取）
# ============================================================

# 是否打印 SQL 日志（开发环境 true，生产环境务必改为 false）
DB_ECHO: bool = os.getenv("DB_ECHO", "true").lower() == "true"

# 连接取出前是否 ping 探测保活
DB_POOL_PRE_PING: bool = os.getenv("DB_POOL_PRE_PING", "true").lower() == "true"

# 连接最大存活秒数（超过后自动回收，配合 MySQL wait_timeout 参数）
DB_POOL_RECYCLE: int = int(os.getenv("DB_POOL_RECYCLE", "3600"))

# 连接池常驻连接数
DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "10"))

# 超过 pool_size 后最多额外创建的临时连接数
DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "20"))

# ============================================================
# 异步 SQLAlchemy 引擎（全局单例）
# ============================================================

async_engine = create_async_engine(
    DATABASE_URL,
    echo=DB_ECHO,
    pool_pre_ping=DB_POOL_PRE_PING,
    pool_recycle=DB_POOL_RECYCLE,
    pool_size=DB_POOL_SIZE,
    max_overflow=DB_MAX_OVERFLOW,
)

# ============================================================
# 异步会话工厂
# ============================================================

# async_sessionmaker 是 SQLAlchemy 2.0 推荐的工厂模式，替代旧版 sessionmaker
# 每次调用 AsyncSessionLocal() 都会创建一个新的 AsyncSession 实例
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    # expire_on_commit=False：提交事务后不过期已加载对象，避免在渲染响应时触发意外的延迟加载查询
    expire_on_commit=False,
)

# ============================================================
# ORM 声明式基类
# ============================================================

class Base(DeclarativeBase):
    """
    SQLAlchemy 2.0 声明式 ORM 模型基类。

    项目中所有数据表模型（如 User、Order、Product 等）必须继承本基类，
    以确保被 Base.metadata 统一管理，便于后续执行 create_all() 自动建表或 Alembic 迁移。

    使用示例：
        from app.database import Base
        from sqlalchemy.orm import Mapped, mapped_column

        class Tenant(Base):
            __tablename__ = "tenants"

            id: Mapped[int] = mapped_column(primary_key=True)
            name: Mapped[str] = mapped_column(String(128), comment="租户名称")
    """

    pass


# ============================================================
# FastAPI 依赖注入 —— 请求级数据库会话
# ============================================================

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI 依赖注入函数：为每个 HTTP 请求提供独立的异步数据库会话。

    通过 async with 上下文管理器创建会话，并在 yield 完成后安全关闭，
    确保即使在请求处理过程中发生未捕获异常，数据库连接也能被正确归还到连接池。

    Yields:
        AsyncSession: 绑定到当前请求生命周期的异步数据库会话。

    使用示例（在路由文件中）：
        from fastapi import APIRouter, Depends
        from app.database import get_db

        router = APIRouter()

        @router.get("/tenants")
        async def list_tenants(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(Tenant))
            return result.scalars().all()

    注意事项：
        - 本函数设计为 FastAPI Depends() 专用，请勿在其他上下文直接调用。
        - yield 之后的 finally 块保证了会话一定会被关闭，即使路由处理中抛出异常。
        - Service 层函数应接收 AsyncSession 作为参数，而不是直接调用本函数。
    """
    # 使用 async with 创建会话上下文，确保 __aexit__ 时自动触发 close()
    async with AsyncSessionLocal() as session:
        try:
            # 将会话交给路由层 / Service 层使用
            yield session
        except Exception:
            # 发生异常时回滚事务，防止脏数据残留
            await session.rollback()
            # 异常继续向上抛出，交由 FastAPI 全局异常处理器统一处理
            raise
        finally:
            # 无条件关闭会话，归还连接到池中
            await session.close()

"""
电商 SaaS AI Agent 平台 —— FastAPI 应用入口

本模块是整个后端服务的启动中枢，职责仅为：
1. 在应用初始化第一时间加载 .env 环境变量（确保后续所有模块能读到配置）
2. 创建 FastAPI 实例并配置 CORS 跨域中间件（为 Streamlit 前端沙盒做准备）
3. 挂载所有业务路由模块（tenant_router / chat_router）

架构红线：
- 严禁在 main.py 中编写任何业务逻辑、模型定义或数据库操作
- 所有配置项必须从 .env 环境变量读取，绝对禁止硬编码
- 路由注册使用 app.include_router()，前缀和 tags 在各自 Router 文件中定义

启动命令：
    uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

访问 Swagger 文档：
    http://127.0.0.1:8000/docs
"""

# ============================================================
# 🔒 第一优先级：环境变量加载（必须在所有业务模块导入之前执行）
#    确保 app.database / app.services 等下游模块在 import 时已能读取到 .env 配置
# ============================================================
from dotenv import load_dotenv

load_dotenv()  # 显式加载项目根目录 .env 文件，任何下游模块导入前必须完成

import os  # noqa: E402 — 紧跟 load_dotenv() 之后，用于读取环境变量

# ============================================================
# FastAPI 核心与中间件
# ============================================================
from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

# ============================================================
# 业务路由模块导入
# ============================================================
from app.routers import chat_router, tenant_router  # noqa: E402

# ============================================================
# FastAPI 应用实例
# ============================================================
app = FastAPI(
    title="电商 SaaS AI Agent 平台",
    description=(
        "多租户智能售后客服系统，基于 LangGraph 图编排引擎 + "
        "MySQL 关系存储 + Redis 高速缓存 + Qdrant 向量知识库。"
        "提供会话日志写入/查询、RAG 知识检索、LLM 意图识别等核心能力。"
    ),
    version="0.1.0",
    docs_url="/docs",        # Swagger UI 路径
    redoc_url="/redoc",      # ReDoc 路径
)

# ============================================================
# CORS 跨域中间件配置
# 当前阶段：允许所有来源（为 Streamlit 前端沙盒开发提供便利）
# 生产环境：务必通过 .env 的 CORS_ORIGINS 变量限定为具体域名
# ============================================================
CORS_ORIGINS_RAW: str = os.getenv("CORS_ORIGINS", "*")
# 支持逗号分隔的多域名，如 "http://localhost:3000,http://localhost:8501"
if CORS_ORIGINS_RAW == "*":
    allowed_origins = ["*"]
else:
    allowed_origins = [origin.strip() for origin in CORS_ORIGINS_RAW.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],    # 允许所有 HTTP 方法（GET / POST / PUT / DELETE / OPTIONS）
    allow_headers=["*"],    # 允许所有请求头（包括 X-Tenant-ID 自定义 Header）
)

# ============================================================
# 路由注册
# ============================================================
app.include_router(chat_router.router)    # /api/v1/chats  —— 会话日志 CRUD（多租户隔离）
app.include_router(tenant_router.router)  # /api/v1/tenants —— 租户管理（平台运维接口）


# ============================================================
# 健康检查端点（Kubernetes liveness / readiness probe 就绪）
# ============================================================
@app.get("/health", tags=["System"])
async def health_check():
    """
    服务健康检查接口

    用于容器编排系统（K8s）的 liveness / readiness 探测，
    以及开发阶段快速验证服务是否已成功启动。

    Returns:
        {"status": "ok", "version": "0.1.0"}
    """
    return {
        "status": "ok",
        "version": app.version,
        "title": app.title,
    }

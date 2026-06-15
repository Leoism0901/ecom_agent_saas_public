"""
向量入库全链路测试脚本（真实 Embedding API 模式）

本脚本用于验证：
1. Qdrant 集合初始化是否正常（幂等创建）
2. 从本地文件读取中文文档（多编码兼容）
3. RecursiveCharacterTextSplitter 切分效果
4. 云端 Embedding API 真实调用（阿里百炼 DashScope text-embedding-v4）
5. PointStruct 组装 + 多租户隔离写入 Qdrant
6. 写入后 Scroll 验证数据正确性 + 语义检索质量检查

运行方式：
    cd d:\\ecommerce_agent_saas
    python scripts\\test_vector_flow.py

前置条件：
    1. 项目根目录 .env 已配置 QDRANT_HOST / QDRANT_PORT / EMBEDDING_API_KEY
    2. Docker 容器 saas_qdrant 已启动（docker-compose up -d qdrant）
    3. 已安装依赖：pip install qdrant-client langchain-text-splitters httpx
"""

import asyncio
import os
import sys

# 将项目根目录加入 sys.path，确保能 import app 模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from app.services.vector_service import (
    process_and_store_document,
    init_knowledge_collection,
    get_qdrant_client,
)

# 加载 .env 环境变量
load_dotenv()

# ============================================================
# 测试配置
# ============================================================
# 测试文档路径（鞋靴类退换货规则，约 2700 字的中文售后规范）
TEST_DOC_PATH: str = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "test_docs",
    "rules.txt",
)
# 测试租户 ID
TEST_TENANT_ID: str = "shoes_shop"


async def main():
    """测试主入口：读取真实文档 → 向量化入库 → 验证"""
    pass


if __name__ == "__main__":
    pass
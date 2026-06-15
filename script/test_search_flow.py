"""
带租户隔离的语义检索测试脚本（真实 Embedding API 模式）

本脚本用于验证：
1. 正确的 tenant_id 能正常检索到相关结果
2. 错误的 tenant_id（模拟跨租户越权访问）返回空结果
3. 多租户 Filter 机制是否从 Qdrant 层面阻断了数据泄露

运行方式：
    cd d:\\ecommerce_agent_saas
    python scripts\\test_search_flow.py

前置条件：
    1. 已运行过 test_vector_flow.py，确保 shoes_shop 租户数据已入库
    2. 项目根目录 .env 已配置 EMBEDDING_API_KEY
    3. Docker 容器 saas_qdrant 已启动
"""

import asyncio
import os
import sys

# 将项目根目录加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from app.services.vector_service import search_knowledge_base

load_dotenv()


async def main():
    """测试主入口：正确租户检索 → 错误租户越权拦截"""
    pass


if __name__ == "__main__":
    pass
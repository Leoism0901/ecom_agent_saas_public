"""
电商高频 FAQ 数据初始化（造数）脚本

本脚本用于将预设的电商常见问答对批量写入 Redis 缓存，为后续 RAG 知识库
检索和 AI Agent 自动应答提供基础数据支撑。

运行方式：
    C:/miniconda/envs/ai_agent_pj1/python.exe scripts/init_faq_data.py

前置条件：
    1. Docker Redis 容器已启动（saas_redis）
    2. 项目根目录 .env 中 Redis 连接配置正确
    3. 已安装依赖：redis、python-dotenv

核心设计：
    - 使用 Redis Pipeline 批量写入，将 N 次网络往返合并为 1 次，大幅提升写入效率
    - 每条 FAQ 设置 24 小时 TTL，模拟生产环境缓存自动淘汰策略
    - Key 使用 faq: 命名空间前缀，便于 SCAN 命令精准匹配与清理
"""

import os
import sys

# ============================================================
# 1. 计算项目根目录并加载 .env 环境变量
# ============================================================
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(PROJECT_ROOT, ".env")

if not os.path.isfile(ENV_FILE):
    print(f"❌ 未找到 .env 文件，期望路径：{ENV_FILE}")
    sys.exit(1)

from dotenv import load_dotenv

load_dotenv(ENV_FILE)

# ============================================================
# 2. 从环境变量读取 Redis 连接配置（绝对禁止硬编码）
# ============================================================
REDIS_HOST = os.getenv("REDIS_HOST")
REDIS_PORT = os.getenv("REDIS_PORT")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
REDIS_DB = os.getenv("REDIS_DB", "0")

# 必填项校验
if not REDIS_HOST:
    print("❌ REDIS_HOST 未配置，请在 .env 中设置")
    sys.exit(1)
if not REDIS_PORT:
    print("❌ REDIS_PORT 未配置，请在 .env 中设置")
    sys.exit(1)

try:
    REDIS_PORT_INT = int(REDIS_PORT)
    REDIS_DB_INT = int(REDIS_DB)
except ValueError as e:
    print(f"❌ Redis 端口或 DB 编号格式非法：{e}")
    sys.exit(1)

# ============================================================
# 3. 定义电商高频 FAQ 数据字典
#    Key 使用 faq: 前缀作为命名空间，便于后续 SCAN 清理与分类管理
#    实际生产环境中，这些 FAQ 可能来自商户后台配置、知识库导入或 AI 自动抽取
# ============================================================
FAQ_DATA: dict[str, str] = {
    # 物流相关 FAQ
    "faq:发什么快递": "亲，我们默认发中通和圆通哦，暂不支持指定快递。",
    # 发货时效 FAQ
    "faq:多久发货": "亲，您拍下付款后，我们会在 48 小时内为您安排发出的呢。",
    # 售后规则 FAQ
    "faq:退换货规则": "支持七天无理由退换货，非质量问题退回运费需买家承担哦。",
}

# 24 小时过期时间（秒），模拟生产环境缓存自动淘汰
FAQ_TTL_SECONDS = 24 * 60 * 60  # 86400 秒


def init_faq_data(
    host: str,
    port: int,
    password: str,
    db: int,
    faq_dict: dict[str, str],
    ttl: int = FAQ_TTL_SECONDS,
) -> list[str]:
    """
    使用 Redis Pipeline 批量写入 FAQ 数据，并为每条记录设置 TTL。

    Pipeline 的优势：
        - 将多个 SET + EXPIRE 命令打包为一次网络往返，避免 N 次 RTT 开销
        - 在批量初始化场景下，性能相比逐条写入可提升 10~50 倍
        - Pipeline 内部按顺序执行，可保证原子性语义（Redis 单线程模型）

    Args:
        host:      Redis 主机地址
        port:      Redis 端口号
        password:  Redis 密码（空字符串表示无密码）
        db:        Redis 数据库编号（0-15）
        faq_dict:  待写入的 FAQ 字典，键为 FAQ Key，值为 FAQ 回答内容
        ttl:       每条 FAQ 的过期时间（秒），默认 86400（24小时）

    Returns:
        成功写入的 Key 列表（按写入顺序排列）

    Raises:
        redis.exceptions.AuthenticationError: 密码认证失败
        redis.exceptions.ConnectionError:     TCP 连接被拒绝或不可达
        redis.exceptions.TimeoutError:        连接或读写超时
    """
    import redis

    # 创建 Redis 客户端（decode_responses=True 自动处理 bytes → str 转换）
    client = redis.Redis(
        host=host,
        port=port,
        password=password if password else None,
        db=db,
        socket_connect_timeout=5,
        socket_timeout=5,
        decode_responses=True,
    )

    # ---------------------------------------------------------
    # 核心：使用 Pipeline 批量写入
    # pipeline() 创建一个命令队列，所有命令先在客户端缓存，
    # 直到 execute() 才一次性发送到 Redis 服务端执行。
    # ---------------------------------------------------------
    pipe = client.pipeline(transaction=False)  # transaction=False 提升批量写入性能
    # （不需要事务回滚语义，纯写入场景关闭 transaction 开销更小）

    for key, value in faq_dict.items():
        # SET key value EX ttl：一条命令完成「写入 + 设置过期时间」
        # 相比 SET + EXPIRE 两次调用，减少一半命令量
        pipe.set(key, value, ex=ttl)

    # execute() 将管道中所有缓冲命令一次性发送执行，返回每个命令的结果列表
    results = pipe.execute()

    # 统计写入结果：set 命令成功时返回 True（或 "OK" 字符串，取决于 redis-py 版本）
    written_keys: list[str] = []
    for key, result in zip(faq_dict.keys(), results):
        if result:
            written_keys.append(key)

    # 优雅关闭连接（归还到连接池）
    client.close()

    return written_keys


# ============================================================
# 4. 主入口：连接 Redis → Pipeline 批量写入 → 输出验证
# ============================================================
if __name__ == "__main__":
    import redis as redis_module  # 仅为异常捕获导入

    print("=" * 60)
    print("  电商 FAQ 数据初始化脚本")
    print("=" * 60)
    print(f"  Redis : {REDIS_HOST}:{REDIS_PORT_INT}  DB={REDIS_DB_INT}")
    print(f"  FAQ 条目数 : {len(FAQ_DATA)}")
    print(f"  TTL      : {FAQ_TTL_SECONDS}s（24小时）")
    print("-" * 60)

    try:
        written = init_faq_data(
            host=REDIS_HOST,
            port=REDIS_PORT_INT,
            password=REDIS_PASSWORD,
            db=REDIS_DB_INT,
            faq_dict=FAQ_DATA,
        )

        if not written:
            print("⚠️  未成功写入任何 FAQ Key，请检查 Redis 连接或数据字典是否为空")
            sys.exit(1)

        # -------------------------------------------------------
        # 输出验证：逐条打印成功写入的 Key，便于人工确认
        # -------------------------------------------------------
        print(f"✅ 成功写入 {len(written)} 条 FAQ 数据：")
        for i, key in enumerate(written, start=1):
            value_preview = FAQ_DATA[key][:40]  # 截取前 40 个字符预览
            print(f"    {i}. {key} → {value_preview}...")

        # -------------------------------------------------------
        # 二次验证：立即从 Redis 回读第一条 Key，确保数据落地
        # -------------------------------------------------------
        verify_client = redis_module.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT_INT,
            password=REDIS_PASSWORD if REDIS_PASSWORD else None,
            db=REDIS_DB_INT,
            socket_connect_timeout=5,
            decode_responses=True,
        )
        first_key = written[0]
        verify_value = verify_client.get(first_key)
        if verify_value is not None:
            print(f"  🔍 回读验证「{first_key}」→ 值存在，数据已落地")
        else:
            print(f"  ⚠️  回读验证「{first_key}」→ Key 不存在！请排查写入是否成功")
        verify_client.close()

        print("-" * 60)
        print("🎉 FAQ 数据初始化完成！")
        print("=" * 60)

    except redis_module.exceptions.AuthenticationError as e:
        print(f"❌ Redis 认证失败：{e}")
        print("   请检查 .env 中 REDIS_PASSWORD 是否与 Docker 容器配置一致")
        sys.exit(1)

    except redis_module.exceptions.ConnectionError as e:
        print(f"❌ Redis 连接失败：{e}")
        print("   请确认 Docker Redis 容器已启动（docker-compose up -d redis）")
        sys.exit(1)

    except redis_module.exceptions.TimeoutError as e:
        print(f"❌ Redis 操作超时：{e}")
        print(f"   连接 {REDIS_HOST}:{REDIS_PORT_INT} 超时，请检查网络或容器状态")
        sys.exit(1)

    except Exception as e:
        print(f"❌ 未知错误：{type(e).__name__} - {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

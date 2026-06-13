"""
Redis 缓存精准清理脚本

本脚本用于按 Key 前缀（prefix）安全地清理 Redis 中的缓存数据。
适用于以下场景：
    - 商户修改 FAQ 后需要刷新对应缓存
    - 测试环境数据重置
    - 特定命名空间下的批量清理（如 faq: / session: / rate_limit: 等）

运行方式：
    # 清理所有 FAQ 缓存（默认）
    C:/miniconda/envs/ai_agent_pj1/python.exe scripts/clear_redis_cache.py

    # 清理指定前缀的缓存
    C:/miniconda/envs/ai_agent_pj1/python.exe scripts/clear_redis_cache.py --prefix "session:*"

安全设计红线（绝对不可妥协）：
    - 本脚本严禁使用 KEYS 命令！KEYS 在遍历全部 Key 时会阻塞 Redis 主线程，
      在生产环境高并发场景下可能导致服务雪崩（Redis 单线程模型下所有请求排队等待）。
    - 必须使用 SCAN 命令（游标迭代），每次只返回少量 Key，不阻塞主线程。
    - SCAN 的 COUNT 参数仅为"建议值"，实际返回数量由 Redis 内部决定，
      因此采用循环迭代直到游标归零。

前置条件：
    1. Docker Redis 容器已启动（saas_redis）
    2. 项目根目录 .env 中 Redis 连接配置正确
    3. 已安装依赖：redis、python-dotenv
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


def scan_and_delete_by_prefix(
    host: str,
    port: int,
    password: str,
    db: int,
    prefix: str,
    scan_count: int = 100,
) -> int:
    """
    使用 Redis SCAN 命令安全地迭代并删除所有匹配指定前缀的 Key。

    为什么必须使用 SCAN 而非 KEYS：
        - Redis 是单线程事件循环模型，KEYS 在遍历所有 Key 时会阻塞整个主线程。
        - 假设生产环境中 Redis 持有 50 万个 Key，KEYS 执行时间可能长达数百毫秒，
          在此期间所有客户端请求（包括在线交易读写）都将排队等待，造成服务雪崩。
        - SCAN 采用游标分步迭代，每次只检查少量 Key 后立即将 CPU 交还事件循环，
          对线上服务的影响几乎可以忽略不计。

    实现细节：
        - 使用 while 循环持续迭代，直到游标 cursor == 0（表示遍历完毕）
        - 每轮 SCAN 返回的 Key 按 prefix 过滤，匹配的 Key 收集到待删除列表
        - 待删除 Key 累积到一定数量后，使用 Pipeline 批量 DELETE，减少网络往返
        - 最后一轮退出循环后，对残留的待删除 Key 做收尾清理

    Args:
        host:       Redis 主机地址
        port:       Redis 端口号
        password:   Redis 密码（空字符串表示无密码）
        db:         Redis 数据库编号（0-15）
        prefix:     待匹配的 Key 前缀，支持通配符风格，如 "faq:*"、"session:*"
        scan_count: 每轮 SCAN 的建议返回数量（并非精确值），默认 100

    Returns:
        成功删除的 Key 总数（int）

    Raises:
        redis.exceptions.AuthenticationError: 密码认证失败
        redis.exceptions.ConnectionError:     TCP 连接被拒绝或不可达
        redis.exceptions.TimeoutError:        连接或读写超时
    """
    import redis

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
    # 预处理 prefix：如果用户传入 "faq:*"，统一处理为匹配模式
    # Redis SCAN 的 match 参数接受通配符：* 匹配任意字符序列
    # 常见用法：faq:* → 匹配所有以 faq: 开头的 Key
    # ---------------------------------------------------------
    deleted_total = 0  # 累计删除总数
    cursor = 0          # SCAN 游标初始值，0 表示第一轮迭代
    pending_keys: list[str] = []  # 待批量删除的 Key 缓冲区

    # ---------------------------------------------------------
    # 第一轮 SCAN：游标为 0 时执行首次迭代
    # 后续每轮使用上一轮返回的游标值继续，直到游标再次归 0
    # ---------------------------------------------------------
    while True:
        # scan(cursor, match, count) → (new_cursor, matched_keys_list)
        cursor, keys = client.scan(
            cursor=cursor,
            match=prefix,
            count=scan_count,
        )

        # 过滤出匹配的 Key 并加入待删除缓冲区
        if keys:
            pending_keys.extend(keys)

        # -------------------------------------------------------
        # 当待删除 Key 累积到一定数量时，使用 Pipeline 批量删除
        # 这样做的好处：
        #   1. 将 N 个 DEL 命令合并为一次网络往返
        #   2. 避免每个 Key 单独 DELETE 产生的 RTT 开销
        #   3. Pipeline 在服务端顺序执行，保证删除语义正确
        # -------------------------------------------------------
        BATCH_SIZE = 200  # 每批最多删除 200 个 Key

        while len(pending_keys) >= BATCH_SIZE:
            batch = pending_keys[:BATCH_SIZE]          # 取出前 BATCH_SIZE 个
            pending_keys = pending_keys[BATCH_SIZE:]   # 剩余继续保留

            pipe = client.pipeline(transaction=False)
            for key in batch:
                pipe.delete(key)
            results = pipe.execute()

            # 统计本批次实际删除数量（DELETE 返回 1=成功删除，0=Key 不存在）
            batch_deleted = sum(1 for r in results if r == 1)
            deleted_total += batch_deleted

        # 游标归零 → 遍历完毕，跳出循环
        if cursor == 0:
            break

    # ---------------------------------------------------------
    # 收尾处理：删除缓冲区中剩余的 Key（不足 BATCH_SIZE 的部分）
    # ---------------------------------------------------------
    if pending_keys:
        pipe = client.pipeline(transaction=False)
        for key in pending_keys:
            pipe.delete(key)
        results = pipe.execute()

        batch_deleted = sum(1 for r in results if r == 1)
        deleted_total += batch_deleted

    client.close()
    return deleted_total


# ============================================================
# 3. 主入口：解析 prefix 参数 → 执行 SCAN + DELETE → 输出结果
# ============================================================
if __name__ == "__main__":
    import redis as redis_module

    # ---------------------------------------------------------
    # 支持命令行参数 --prefix 指定匹配模式，不传则默认清理 FAQ 缓存
    # 用法示例：
    #   python clear_redis_cache.py --prefix "session:*"
    #   python clear_redis_cache.py --prefix "faq:*"
    # ---------------------------------------------------------
    DEFAULT_PREFIX = "faq:*"

    # 简易命令行参数解析（无需引入 argparse，保持脚本零额外依赖）
    prefix = DEFAULT_PREFIX
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--prefix" and i + 1 < len(args):
            prefix = args[i + 1]
            break

    # ---------------------------------------------------------
    # 安全确认：清理操作具有破坏性，打印确认信息让使用者知晓影响范围
    # ---------------------------------------------------------
    print("=" * 60)
    print("  Redis 缓存精准清理脚本")
    print("=" * 60)
    print(f"  Redis  : {REDIS_HOST}:{REDIS_PORT_INT}  DB={REDIS_DB_INT}")
    print(f"  匹配模式: {prefix}")
    print("-" * 60)

    # 先探活，确保 Redis 可达
    try:
        probe = redis_module.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT_INT,
            password=REDIS_PASSWORD if REDIS_PASSWORD else None,
            db=REDIS_DB_INT,
            socket_connect_timeout=5,
            decode_responses=True,
        )
        probe.ping()
        probe.close()
        print("✅ Redis 连接正常")
    except redis_module.exceptions.ConnectionError as e:
        print(f"❌ Redis 连接失败：{e}")
        sys.exit(1)
    except redis_module.exceptions.AuthenticationError as e:
        print(f"❌ Redis 认证失败：{e}")
        sys.exit(1)

    # ---------------------------------------------------------
    # 执行 SCAN + 批量删除
    # ---------------------------------------------------------
    try:
        deleted_count = scan_and_delete_by_prefix(
            host=REDIS_HOST,
            port=REDIS_PORT_INT,
            password=REDIS_PASSWORD,
            db=REDIS_DB_INT,
            prefix=prefix,
        )

        if deleted_count == 0:
            print(f"ℹ️  未找到匹配「{prefix}」的 Key，无需清理")
        else:
            print(f"🧹 成功清理 {deleted_count} 个匹配「{prefix}」的 Key")

        print("-" * 60)
        print("🎉 缓存清理完成！")
        print("=" * 60)

    except redis_module.exceptions.RedisError as e:
        print(f"❌ Redis 运行时错误：{type(e).__name__} - {e}")
        sys.exit(1)

    except Exception as e:
        print(f"❌ 未知错误：{type(e).__name__} - {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

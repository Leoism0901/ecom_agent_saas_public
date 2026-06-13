"""
Redis 环境连通性测试脚本

本脚本用于验证项目本地 Docker Redis 容器是否正常运行、网络是否可达、
密码认证是否正确。属于「第四天第一阶段」的基础设施探活任务。

运行方式：
    cd d:\\ecommerce_agent_saas
    python scripts\\test_redis_conn.py

前置条件：
    1. 项目根目录下存在 .env 文件，且已配置 REDIS_HOST / REDIS_PORT 等变量
    2. Docker 容器 saas_redis 已启动（docker-compose up -d redis）
    3. 已安装依赖：pip install redis python-dotenv
"""

import os
import sys

# ============================================================
# 1. 环境变量加载（必须在所有业务逻辑之前完成）
#    从项目根目录 .env 文件中读取 Redis 连接配置
#    os.path.dirname(__file__) → scripts/
#    os.path.dirname(…) 再一次 → 项目根目录
# ============================================================
from dotenv import load_dotenv

# 计算项目根目录的绝对路径（本脚本位于 scripts/ 子目录下）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(PROJECT_ROOT, ".env")

if not os.path.isfile(ENV_FILE):
    print(f"❌ 未找到 .env 文件，期望路径：{ENV_FILE}")
    print("   请确认项目根目录下存在 .env 配置文件（可复制 .env.example 并填入真实值）")
    sys.exit(1)

# 显式加载指定路径的 .env 文件
load_dotenv(ENV_FILE)

# ============================================================
# 2. 从环境变量读取 Redis 连接配置（绝对禁止硬编码）
#    - HOST / PORT 为必填项，缺失时给出明确提示并退出
#    - PASSWORD 为可选项（本地开发环境通常留空），缺省为空字符串
#    - DB 为可选项，缺省为 0
# ============================================================
REDIS_HOST = os.getenv("REDIS_HOST")
REDIS_PORT = os.getenv("REDIS_PORT")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")  # 密码可为空（本地开发环境）
REDIS_DB = os.getenv("REDIS_DB", "0")

# 必填项校验
if not REDIS_HOST:
    print("❌ 环境变量 REDIS_HOST 未配置，请在 .env 中设置 Redis 主机地址")
    sys.exit(1)

if not REDIS_PORT:
    print("❌ 环境变量 REDIS_PORT 未配置，请在 .env 中设置 Redis 端口号")
    sys.exit(1)

# 端口号必须为合法整数
try:
    REDIS_PORT_INT = int(REDIS_PORT)
except ValueError:
    print(f"❌ 环境变量 REDIS_PORT 值非法：'{REDIS_PORT}'，必须为有效整数")
    sys.exit(1)

# DB 编号必须为合法整数
try:
    REDIS_DB_INT = int(REDIS_DB)
except ValueError:
    print(f"❌ 环境变量 REDIS_DB 值非法：'{REDIS_DB}'，必须为有效整数（0-15）")
    sys.exit(1)

# ============================================================
# 3. 打印连接信息（密码脱敏，只显示首尾字符用于确认配置正确性）
# ============================================================
password_display = "（空）" if not REDIS_PASSWORD else f"{REDIS_PASSWORD[:1]}***{REDIS_PASSWORD[-1:] if len(REDIS_PASSWORD) > 1 else ''}"
print("=" * 60)
print("  Redis 环境连通性测试")
print("=" * 60)
print(f"  Host    : {REDIS_HOST}")
print(f"  Port    : {REDIS_PORT_INT}")
print(f"  Password: {password_display}")
print(f"  DB      : {REDIS_DB_INT}")
print("-" * 60)

# ============================================================
# 4. 建立 Redis 连接并执行读写探活
#    核心流程：连接 → PING 探活 → 写入带 TTL 的测试 Key → 读取验证
#    异常处理：捕获连接超时、认证失败、网络不可达等常见故障
# ============================================================
import redis  # noqa: E402 — 在环境变量加载完成后才导入，确保配置就绪

try:
    # ---------------------------------------------------------
    # 4.1 创建 Redis 客户端实例
    #     - socket_connect_timeout=5：连接超时 5 秒，避免长时间挂起
    #     - decode_responses=True：读写时自动将 bytes 解码为 str，避免手动 decode
    # ---------------------------------------------------------
    redis_client = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT_INT,
        password=REDIS_PASSWORD if REDIS_PASSWORD else None,  # 空字符串 → None，避免误传
        db=REDIS_DB_INT,
        socket_connect_timeout=5,   # TCP 连接超时（秒）
        socket_timeout=5,           # 读写操作超时（秒）
        decode_responses=True,      # 自动将 bytes 解码为 Python str
    )

    # ---------------------------------------------------------
    # 4.2 PING 探活：发送 Redis PING 命令，预期返回 True
    #     如果 Redis 不可达或密码错误，此处会抛出异常，跳过后续写入
    # ---------------------------------------------------------
    ping_result = redis_client.ping()
    if not ping_result:
        print("❌ Redis PING 命令未返回预期结果，连接可能异常")
        sys.exit(1)

    print("✅ Redis PING 探活成功")

    # ---------------------------------------------------------
    # 4.3 写入测试 Key（带 10 秒 TTL）
    #     - Key：test:ping（使用命名空间前缀 test:，避免与业务 Key 冲突）
    #     - Value：pong（经典探活值，便于一眼识别测试数据）
    #     - ex=10：10 秒后自动过期删除，防止脚本异常退出遗留垃圾数据
    # ---------------------------------------------------------
    TEST_KEY = "test:ping"
    TEST_VALUE = "pong"
    TTL_SECONDS = 10

    redis_client.set(TEST_KEY, TEST_VALUE, ex=TTL_SECONDS)
    print(f"✅ 写入 Key 成功：{TEST_KEY} = {TEST_VALUE}（TTL={TTL_SECONDS}s）")

    # ---------------------------------------------------------
    # 4.4 立即读取测试 Key，验证写入是否生效
    # ---------------------------------------------------------
    read_value = redis_client.get(TEST_KEY)

    if read_value is None:
        # 理论上不会发生（刚刚写入才过了几毫秒），但防御性编程不可少
        print("⚠️  读取 Key 返回 None，测试 Key 可能已意外过期或被删除")
        sys.exit(1)

    if read_value != TEST_VALUE:
        print(f"⚠️  读取到的值与写入值不一致！期望：'{TEST_VALUE}'，实际：'{read_value}'")
        sys.exit(1)

    print(f"✅ 读取 Key 成功：{TEST_KEY} = {read_value}")

    # ---------------------------------------------------------
    # 4.5 验证 TTL 已正确设置
    # ---------------------------------------------------------
    actual_ttl = redis_client.ttl(TEST_KEY)
    print(f"✅ 剩余 TTL：{actual_ttl} 秒（预期约 {TTL_SECONDS}s）")

    # ---------------------------------------------------------
    # 4.6 主动清理测试 Key（优雅收尾，不留垃圾）
    # ---------------------------------------------------------
    redis_client.delete(TEST_KEY)
    print(f"🧹 已主动清理测试 Key：{TEST_KEY}")

    # ============================================================
    # 全部通过，输出汇总成功信息
    # ============================================================
    print("-" * 60)
    print(f"🎉 Redis 连接成功！读取测试数据：{read_value}")
    print(f"   连接地址：{REDIS_HOST}:{REDIS_PORT_INT}，DB={REDIS_DB_INT}")
    print("=" * 60)

except redis.exceptions.AuthenticationError as e:
    # 密码错误 / 认证失败
    print(f"❌ Redis 认证失败：{e}")
    print("   请检查 .env 中 REDIS_PASSWORD 是否正确配置")
    print("   Docker 容器默认密码可通过 docker-compose.yml 中的 requirepass 参数确认")
    sys.exit(1)

except redis.exceptions.ConnectionError as e:
    # TCP 连接被拒绝 / 网络不可达
    print(f"❌ Redis 连接失败：{e}")
    print("   常见原因排查：")
    print(f"   1. Docker 容器是否已启动？  运行：docker ps | findstr redis")
    print(f"   2. 端口 {REDIS_PORT_INT} 是否被其他进程占用？")
    print(f"   3. .env 中 REDIS_HOST={REDIS_HOST} 是否正确？")
    print(f"   4. 如果 Redis 在容器内，主机地址应为 localhost（本地开发）或容器服务名（生产）")
    sys.exit(1)

except redis.exceptions.TimeoutError as e:
    # 连接或读写超时
    print(f"❌ Redis 操作超时：{e}")
    print(f"   连接地址 {REDIS_HOST}:{REDIS_PORT_INT} 在 5 秒内无响应")
    print("   请检查防火墙规则或 Docker 容器是否正常运行")
    sys.exit(1)

except redis.exceptions.RedisError as e:
    # 其他 Redis 客户端异常（兜底捕获）
    print(f"❌ Redis 未知运行时错误：{type(e).__name__} - {e}")
    sys.exit(1)

except Exception as e:
    # 非预期异常（兜底，防止脚本直接崩溃无任何输出）
    print(f"❌ 未知错误：{type(e).__name__} - {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

finally:
    # finally 块中不再重复 close()，因为 redis.Redis 在 __del__ 时会自动清理连接
    # 显式关闭连接池（如需要）可通过 redis_client.close() 实现，但测试脚本生命周期短，可省略
    pass

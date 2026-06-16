"""
工具模块包

本目录存放跨业务复用的纯工具类与 FastAPI 依赖项，如：
- rate_limiter：滑动窗口限流器（Redis ZSET）
- redis_memory：短期对话记忆存储（Redis List，支持自动截断 + TTL 过期）
- (后续扩展) auth：JWT 鉴权依赖
"""

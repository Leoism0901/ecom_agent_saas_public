"""
Agent 工具模块包（Tool-Calling 专用）

本目录存放供大模型 (豆包/Claude 等) 进行 Function Calling 时调用的本地 Mock 工具函数。
所有函数均为纯 Python 本地实现，不涉及数据库连接或外部 API 请求，
返回结果为结构化 JSON 字典，便于大模型读取与解析。

目录结构：
- ecommerce_mocks.py：电商后台模拟工具（订单状态查询、退款拦截、运费规则查询）
"""

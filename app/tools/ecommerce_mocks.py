"""
电商后台 Mock 工具函数（专为大模型 Tool-Calling 设计）

本模块是 AI 售后 Agent 的「虚拟后台」—— 提供 3 个本地模拟函数，用于在 Agent 框架中
模拟真实电商后台系统的查询与操作。所有返回数据均为硬编码的 Python 字典（dict），
绝不触及数据库或外部 API，确保 Tool-Calling 调试阶段零外部依赖、零副作用。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
设计原则（架构红线，严禁违反）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. 返回结构统一：每个函数返回 {"status": str, "message": str, "data": dict}
2. 针对特定参数返回预设剧本数据，覆盖「正常 / 异常 / 默认」三种分支
3. 所有注释与字段描述均使用中文，遵循项目编码规范
4. 每个函数的 Pydantic Input Model 即为大模型的 Tool Schema —— 大模型完全依赖
   args_schema 生成的 JSON Schema 来决定调用时机、参数提取方式与返回值解读策略

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
大模型 Tool-Calling 集成说明
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- 本模块为每个函数定义了 Pydantic BaseModel 作为 args_schema，供 LangChain 的
  @tool 装饰器或 StructuredTool 工厂函数直接引用，自动生成精准的 JSON Schema
- 豆包 1.6 / Claude 等大模型通过 JSON Schema 构建 Function Calling 的 tools 数组
- 大模型根据用户消息语义，自行判断是否命中某个函数的「触发意图」，
  若命中则自动生成 function_call 并提取所需参数
- 本模块函数被调用后返回的 dict，由 Agent 框架原样注入回大模型的上下文，
  大模型从中提取关键字段（status / message / data.*）生成自然语言客服回复
- 接入示例（在 Agent 编排层统一绑定）：
      from langchain_core.tools import tool
      from app.tools.ecommerce_mocks import get_order_status, OrderStatusInput

      @tool(args_schema=OrderStatusInput)
      def get_order_status_tool(order_id: str) -> dict:
          return get_order_status(order_id)
"""

from typing import Optional

from pydantic import BaseModel, Field


# ============================================================
# 0. Pydantic 输入校验模型（大模型 Tool Schema 自动生成源）
# ============================================================

class OrderStatusInput(BaseModel):
    """
    订单状态查询工具的输入参数 Schema。
    由 LangChain @tool(args_schema=OrderStatusInput) 自动转换为 JSON Schema 传给大模型。
    """
    order_id: str = Field(
        ...,
        description=(
            "订单编号，格式为 'ORD-{数字}'（如 'ORD-123'），大小写严格敏感。"
            "大模型应从买家对话中提取订单号，提取规则："
            "买家输入 'ORD-123'、'订单123'、'123号订单' → 均归一化为 'ORD-123'；"
            "买家未提供订单号 → 不可调用本工具，应向买家询问订单号。"
            "约束：必须以 'ORD-' 开头、后接数字的字符串。"
        ),
        pattern=r"^ORD-\d+$",
        examples=["ORD-123", "ORD-404"],
    )


class RefundItemInput(BaseModel):
    """
    退款/退货拦截工具的输入参数 Schema。
    由 LangChain @tool(args_schema=RefundItemInput) 自动转换为 JSON Schema 传给大模型。
    """
    order_id: str = Field(
        ...,
        description=(
            "订单编号，格式为 'ORD-{数字}'（如 'ORD-999'），大小写严格敏感。"
            "大模型提取规则同 get_order_status。"
        ),
        pattern=r"^ORD-\d+$",
        examples=["ORD-123", "ORD-999"],
    )
    reason: str = Field(
        default="",
        description=(
            "买家填写的退款原因，大模型从对话中提取后填入。常见取值："
            "'不想要了' / '商品破损' / '发错货了' / '质量问题' / '与描述不符' / '迟迟未发货' / ''（未说明）。"
            "该参数当前不参与 Mock 分支路由逻辑（仅 order_id 决定返回结果），"
            "但保留此参数供后续真实退款系统接入时使用，届时 reason 将影响审核结果。"
        ),
        examples=["商品破损", "不想要了"],
    )


class CheckShippingRulesInput(BaseModel):
    """
    运费与发货规则查询工具的输入参数 Schema。
    由 LangChain @tool(args_schema=CheckShippingRulesInput) 自动转换为 JSON Schema 传给大模型。
    """
    item_id: str = Field(
        ...,
        description=(
            "商品编号 / SKU 编码，字符串类型。当前版本所有商品共用同一套运费模板"
            "（即 item_id 不影响 Mock 返回结果），但保留此参数供后续按商品差异化定价扩展。"
            "大模型从对话上下文中提取商品 ID，若无法获取可传空字符串 '' 或 'ITEM-UNKNOWN'。"
        ),
        examples=["ITEM-001", "ITEM-UNKNOWN"],
    )
    province: str = Field(
        ...,
        description=(
            "收货省份名称。大模型从买家消息中提取，提取与归一化规则："
            "买家说'新疆' / '新疆维吾尔自治区' / '新疆乌鲁木齐' → province='新疆'；"
            "买家说'西藏' / '西藏自治区' / '拉萨' → province='西藏'；"
            "买家说'内蒙古' / '内蒙古自治区' / '呼和浩特' → province='内蒙古'；"
            "买家说'广东' / '广东省' / '广州' → province='广东省'。"
            "核心原则：函数内部使用子串匹配判断偏远地区（含'新疆'/'西藏'/'内蒙古'即命中），"
            "因此传入 '新疆维吾尔自治区' 和 '新疆' 效果相同，但建议传入标准化省份简称以提高匹配效率。"
        ),
        min_length=1,
        examples=["广东省", "新疆", "西藏"],
    )


# ============================================================
# 1. 订单状态查询
# ============================================================

def get_order_status(order_id: str) -> dict:
    """
    **订单物流状态查询** —— 查询指定订单的当前物流配送进度与快递承运信息。

    ### 触发意图（大模型 Tool-Calling 核心决策依据）

    大模型在接收到买家消息时，若消息语义匹配以下任一场景，**必须**调用本工具：

    **场景 A · 主动查物流：**
    - "我的快递到哪了"、"查一下物流"、"包裹到哪里了"
    - "帮我看看订单到哪了"、"物流信息"、"快递进度"

    **场景 B · 发货时效询问：**
    - "发货了吗"、"什么时候发货"、"怎么还没发"
    - "多久能到"、"预计什么时候送达"、"还要几天"

    **场景 C · 异常物流反馈：**
    - "物流怎么不动了"、"快递卡住了"、"好几天没更新了"
    - "包裹是不是丢了"、"物流异常"

    **场景 D · 买家提供订单号并要求查询：**
    - "ORD-123 查一下"、"订单123的状态"、"帮我查ORD-404"

    **注意：** 若买家未提供订单号，大模型应先引导买家提供订单号后再调用本工具。

    ### Args
    - **order_id** (str): 订单编号，格式为 "ORD-{数字}"（如 "ORD-123"），大小写严格敏感。
      大模型提取规则：买家输入 "ORD-123"、"订单123"、"123号订单" → 均归一化为 "ORD-123"。
      约束：必须以 "ORD-" 开头、后接数字的字符串，否则返回默认处理中状态。

    ### Returns
    dict —— 统一 JSON 响应体，大模型生成回复时按以下路径提取数据：

    **顶层键（大模型回复话术生成指引）：**
    - `ret["status"]` (str):
        - `"success"` → 物流正常，告知买家当前派送状态与预计送达时间
        - `"warning"` → 物流异常，共情买家焦虑情绪，建议联系物流客服
        - `"info"`    → 订单处理中，安抚买家耐心等待，说明待揽收现状
    - `ret["message"]` (str): 人类可读的状态摘要，可直接嵌入客服回复话术
    - `ret["data"]` (dict): 结构化详情，含 order_id / order_status / logistics_info 等字段

    **data 子字段说明：**
    - `order_id` (str): 订单编号（原样回传，用于核对）
    - `order_status` (str): 枚举值 `shipped` / `stalled` / `processing`
    - `logistics_info` (dict):
        - `carrier` (str): 快递公司名称
        - `tracking_number` (str): 快递单号
        - `current_status` (str): 当前物流节点描述
        - `estimated_delivery` (str): 预计送达日期 (YYYY-MM-DD)
        - `last_update` (str): 最后物流更新时间
        - `stagnant_hours` (float): 仅异常时，已停滞小时数
        - `last_scan_location` (str): 仅异常时，最后扫描位置

    ### 异常模拟（用于 Agent 鲁棒性测试）
    - `order_id == "ORD-500"` → 触发 `TimeoutError`，模拟物流系统后端超时
    - `order_id == "ORD-501"` → 触发 `ValueError`，模拟数据格式异常
    """
    pass


# ============================================================
# 2. 退款 / 退货拦截
# ============================================================

def refund_item(order_id: str, reason: str = "") -> dict:
    """
    **退款 / 退货拦截** —— 对指定订单发起退款或退货拦截操作，返回处理结果与买家指引。

    ### 触发意图（大模型 Tool-Calling 核心决策依据）

    大模型在接收到买家消息时，若消息语义匹配以下任一场景，**必须**调用本工具：

    **场景 A · 买家主动要求退款 / 退货 / 仅退款：**
    - "我要退款"、"申请退货"、"不想要了帮我退"
    - "怎么退款"、"退货退款"、"仅退款"、"取消订单"

    **场景 B · 买家表达不满并要求补偿：**
    - "东西坏了我要退"、"质量太差退款吧"、"收到的和图片不一样"
    - "我要投诉并退款"、"不满意想退货"、"退钱"

    **场景 C · 买家询问退款进度：**
    - "我的退款处理了吗"、"退款到哪一步了"、"钱什么时候退回来"
    - "退货审核通过了吗"、"退款进度查一下"

    **场景 D · 买家提供订单号 + 退款诉求：**
    - "ORD-999 申请退款"、"订单123不要了退一下"

    **注意：** reason 参数为可选，大模型应从买家消息中提取退款原因填入，若未明确说明则传空字符串。

    ### Args
    - **order_id** (str): 订单编号，格式为 "ORD-{数字}"（如 "ORD-123"），大小写严格敏感。
      大模型提取规则：买家输入 "订单123" → 归一化为 "ORD-123"。
    - **reason** (str, 可选): 买家填写的退款原因，大模型从对话中提取后填入。
      常见取值："不想要了" / "商品破损" / "发错货了" / "质量问题" / "与描述不符" / "迟迟未发货" / ""。
      该参数当前不参与 Mock 分支路由逻辑（仅 order_id 决定返回结果），
      但保留此参数供后续真实退款系统接入时使用，届时 reason 将影响审核结果。

    ### Returns
    dict —— 统一 JSON 响应体，大模型生成回复时按以下路径提取数据：

    **顶层键（大模型回复话术生成指引）：**
    - `ret["status"]` (str):
        - `"success"` → 退款成功，告知买家金额、渠道与预计到账时间
        - `"failed"`  → 退款拦截失败，清晰说明原因并给出替代操作路径
        - `"pending"` → 审核中，告知买家预计处理时效，安抚等待情绪
    - `ret["message"]` (str): 退款处理结果的人类可读摘要，可直接嵌入客服话术
    - `ret["data"]` (dict): 结构化退款详情，含 order_id / refund_status / refund_amount 等字段

    **data 子字段说明：**
    - `order_id` (str): 订单编号（原样回传）
    - `refund_status` (str): 枚举值 `refunded` / `intercept_failed` / `under_review`
    - `refund_amount` (float): 退款金额（元）
    - `refund_channel` (str): 退款渠道（如微信支付原返）
    - `reason_accepted` (bool): 系统是否接受退款原因
    - `expected_processing_hours` (int): 仅审核中时，预计处理时长
    - `estimated_arrival` (str): 仅已退款时，到账时效说明
    - `next_step` (str): 买家下一步操作指引

    ### 异常模拟（用于 Agent 鲁棒性测试）
    - `order_id == "ORD-500"` → 触发 `TimeoutError`，模拟退款系统后端超时
    """
    pass


# ============================================================
# 3. 运费规则查询
# ============================================================

def check_shipping_rules(item_id: str, province: str) -> dict:
    """
    **运费与发货规则查询** —— 根据商品 ID 与收货省份，查询是否包邮、运费金额及预计发货时效。

    ### 触发意图（大模型 Tool-Calling 核心决策依据）

    大模型在接收到买家消息时，若消息语义匹配以下任一场景，**必须**调用本工具：

    **场景 A · 买家询问运费相关：**
    - "包邮吗"、"运费多少"、"要不要邮费"
    - "新疆包邮吗"、"内蒙古运费多少钱"、"西藏发货要加钱吗"
    - "偏远地区包不包邮"、"这边要补运费吗"

    **场景 B · 买家询问发货时效：**
    - "多久能发货"、"什么时候发"、"几天能发"
    - "今天能发吗"、"付款后多久发货"、"发货时间"

    **场景 C · 买家提供省份地址 + 运费 / 时效疑问：**
    - "我在广东，包邮吗"、"发到西藏要几天"
    - "收货地址是新疆乌鲁木齐，运费怎么算"

    **注意：** 大模型必须从买家消息中提取 province 参数（省份名）；若买家仅提及城市（如"乌鲁木齐"），
    大模型应映射为对应省份（如"新疆"）后再调用；若买家未提及任何地址信息，大模型应先询问收货地区。

    ### Args
    - **item_id** (str): 商品编号 / SKU 编码。当前版本所有商品共用同一套运费模板
      （即 item_id 不影响 Mock 返回结果），但保留此参数供后续按商品差异化定价扩展。
      大模型从对话上下文中提取商品 ID，若无法获取可传空字符串 "" 或 "ITEM-UNKNOWN"。
    - **province** (str): 收货省份名称。大模型从买家消息中提取，提取与归一化规则：
        - 买家说"新疆" / "新疆维吾尔自治区" / "新疆乌鲁木齐" → province="新疆"
        - 买家说"西藏" / "西藏自治区" / "拉萨" → province="西藏"
        - 买家说"内蒙古" / "内蒙古自治区" / "呼和浩特" → province="内蒙古"
        - 买家说"广东" / "广东省" / "广州" → province="广东省"
      **核心原则：** 函数内部使用子串匹配判断偏远地区（含"新疆"/"西藏"/"内蒙古"即命中），
      因此传入 "新疆维吾尔自治区" 和 "新疆" 效果相同，但建议传入标准化省份简称。

    ### Returns
    dict —— 统一 JSON 响应体，大模型生成回复时按以下路径提取数据：

    **顶层键（大模型回复话术生成指引）：**
    - `ret["status"]` (str):
        - `"free_shipping"` → 包邮，告知买家零运费与发货时效
        - `"shipping_fee_required"` → 不包邮，清晰说明需补运费金额
    - `ret["message"]` (str): 运费规则的人类可读摘要，可直接用于客服回复
    - `ret["data"]` (dict): 结构化运费详情，含 item_id / province / shipping_fee 等字段

    **data 子字段说明：**
    - `item_id` (str): 商品编号（原样回传）
    - `province` (str): 收货省份（原样回传）
    - `is_remote` (bool): 是否为偏远地区（新疆/西藏/内蒙古 → True）
    - `shipping_fee` (float): 运费金额（元），包邮时为 0.00
    - `free_shipping` (bool): 是否包邮
    - `estimated_ship` (str): 预计发货时效描述（如"48小时内"）
    - `note` (str): 补充说明（大促时效顺延 / 偏远延迟）

    ### 异常模拟（用于 Agent 鲁棒性测试）
    - `item_id == "ITEM-ERROR"` → 触发 `RuntimeError`，模拟运费规则引擎内部异常
    """
    pass


# ============================================================
# 本地验收测试块（仅 python app/tools/ecommerce_mocks.py 时触发）
# 不依赖任何外部服务，纯本地 print + json 输出验证
# ============================================================
if __name__ == "__main__":
    pass
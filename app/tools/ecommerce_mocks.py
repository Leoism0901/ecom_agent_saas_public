"""
电商后台 Mock 工具函数（专为大模型 Tool-Calling 设计）

本模块提供 3 个本地模拟函数，用于在 Agent 框架中模拟真实电商后台系统的查询与操作。
所有返回数据均为硬编码的 Python 字典（dict），绝不触及数据库或外部 API，
确保 Tool-Calling 调试阶段零外部依赖、零副作用。

设计原则：
1. 返回结构统一：每个函数返回 {"status": str, "message": str, "data": dict}
2. 针对特定参数返回预设剧本数据，覆盖"正常 / 异常 / 默认"三种分支
3. 所有注释与字段描述均使用中文，遵循项目编码规范

使用场景：
- 大模型 (豆包 1.6) 通过 Function Calling 调用本模块函数，获取模拟结果
- 前端 / 测试脚本直接 import 调用，验证 Tool-Calling 全链路
"""

from typing import Optional


# ============================================================
# 1. 订单状态查询
# ============================================================

def get_order_status(order_id: str) -> dict:
    """
    根据订单 ID 查询当前订单的物流与配送状态。

    本函数模拟电商后台订单查询接口，针对特定 order_id 返回预设的假数据剧本：
    - "ORD-123"：正常已发货，顺丰速运正在派送
    - "ORD-404"：异常停滞，包裹在转运中心滞留超过 72 小时
    - 其他 ID：默认处理中状态，等待揽收

    Args:
        order_id: 订单编号，格式如 "ORD-XXX"（字符串类型，大小写敏感）

    Returns:
        dict，结构如下：
        {
            "status":  "success" | "warning" | "error",
            "message": 人类可读的状态摘要,
            "data": {
                "order_id":      订单编号（原样回传）,
                "order_status":   当前订单状态枚举值,
                "logistics_info": 物流详情字典,
            }
        }
    """
    # ---------------------------------------------------------
    # 剧本分支一：ORD-123 —— 已发货，顺丰速运正在派送
    # ---------------------------------------------------------
    if order_id == "ORD-123":
        return {
            "status": "success",
            "message": "订单已发货，顺丰速运正在派送中，预计今日送达。",
            "data": {
                "order_id": "ORD-123",
                "order_status": "shipped",  # 已发货
                "logistics_info": {
                    "carrier": "顺丰速运",          # 承运快递公司
                    "tracking_number": "SF1234567890",  # 快递单号
                    "current_status": "派送中",     # 当前物流节点状态
                    "estimated_delivery": "2026-06-15",  # 预计送达日期
                    "last_update": "2026-06-14 09:30:00",  # 最后更新时间
                },
            },
        }

    # ---------------------------------------------------------
    # 剧本分支二：ORD-404 —— 异常停滞，转运中心滞留超 72 小时
    # ---------------------------------------------------------
    if order_id == "ORD-404":
        return {
            "status": "warning",
            "message": "订单异常：包裹在转运中心停滞超过 72 小时，建议联系物流客服核查。",
            "data": {
                "order_id": "ORD-404",
                "order_status": "stalled",  # 异常停滞
                "logistics_info": {
                    "carrier": "中通快递",
                    "tracking_number": "ZTO9876543210",
                    "current_status": "转运中心滞留",  # 停滞节点
                    "stagnant_hours": 72.5,  # 已停滞时长（小时）
                    "last_scan_location": "华南转运中心（广州）",  # 最后扫描位置
                    "last_update": "2026-06-11 06:15:00",  # 最后更新时间（72 小时前）
                },
            },
        }

    # ---------------------------------------------------------
    # 默认分支：订单处理中，待揽收
    # 覆盖所有未预设剧本的 order_id（如 ORD-001、ORD-999 等）
    # ---------------------------------------------------------
    return {
        "status": "info",
        "message": f"订单 {order_id} 正在处理中，等待仓库揽收，请稍后关注物流更新。",
        "data": {
            "order_id": order_id,
            "order_status": "processing",  # 处理中
            "logistics_info": {
                "carrier": "待分配",         # 尚未分配快递公司
                "tracking_number": None,     # 暂无快递单号
                "current_status": "待揽收",  # 仓库尚未出库
                "estimated_delivery": "待定",
                "last_update": None,
            },
        },
    }


# ============================================================
# 2. 退款 / 退货拦截
# ============================================================

def refund_item(order_id: str, reason: str = "") -> dict:
    """
    根据订单 ID 与退款原因发起退款 / 退货拦截操作。

    模拟退款系统对不同物流状态下订单的处理策略：
    - "ORD-123"（已发货）：拦截失败，包裹已在途中，引导买家拒收
    - "ORD-999"（未发货）：拦截成功，退款即时处理并原路返回
    - 其他 ID：进入审核流程，预计 24 小时内完成

    Args:
        order_id: 订单编号，格式如 "ORD-XXX"
        reason:   买家填写的退款原因（可选），如 "不想要了" / "商品破损" 等。
                  该参数参与业务判断但不影响当前 Mock 分支逻辑。

    Returns:
        dict，结构如下：
        {
            "status":   "success" | "pending" | "failed",
            "message":  退款处理结果的人类可读摘要,
            "data": {
                "order_id":       订单编号（原样回传）,
                "refund_status":   退款状态枚举值,
                "refund_amount":   退款金额（元）,
                "refund_channel":  退款渠道,
                "reason_accepted": 系统是否接受该退款原因,
                "next_step":       买家下一步操作指引,
            }
        }
    """
    # ---------------------------------------------------------
    # 剧本分支一：ORD-123 —— 已发货，拦截失败，引导买家拒收
    # ---------------------------------------------------------
    if order_id == "ORD-123":
        return {
            "status": "failed",
            "message": "退款拦截失败：包裹已出库并在运输途中，无法中途召回。请引导买家在快递到达时拒收，拒收后系统将自动触发退款。",
            "data": {
                "order_id": "ORD-123",
                "refund_status": "intercept_failed",  # 拦截失败
                "refund_amount": 0.0,  # 尚未退款，金额暂为 0
                "refund_channel": None,
                "reason_accepted": True,  # 退款原因合理，但物流状态不允许直接退款
                "next_step": "引导买家拒收包裹，拒收后系统自动触发退款流程，款项 3-5 个工作日原路返回。",
            },
        }

    # ---------------------------------------------------------
    # 剧本分支二：ORD-999 —— 未发货，退款成功，原路返回
    # ---------------------------------------------------------
    if order_id == "ORD-999":
        return {
            "status": "success",
            "message": "退款成功：订单未发货，拦截已生效，款项将原路返回至买家支付账户。",
            "data": {
                "order_id": "ORD-999",
                "refund_status": "refunded",  # 已退款
                "refund_amount": 299.00,  # 模拟退款金额（元）
                "refund_channel": "微信支付原路返回",  # 原支付渠道退回
                "reason_accepted": True,
                "estimated_arrival": "预计 1-3 个工作日内到账",  # 到账时效说明
                "next_step": "退款已提交银行处理，请提醒买家留意支付账户到账通知。",
            },
        }

    # ---------------------------------------------------------
    # 默认分支：退款审核中，24 小时内出结果
    # 覆盖所有未预设剧本的 order_id
    # ---------------------------------------------------------
    return {
        "status": "pending",
        "message": f"订单 {order_id} 的退款申请已提交，审核中，预计 24 小时内完成处理。",
        "data": {
            "order_id": order_id,
            "refund_status": "under_review",  # 审核中
            "refund_amount": 0.0,  # 审核未完成，金额待定
            "refund_channel": None,
            "reason_accepted": True,
            "expected_processing_hours": 24,  # 预计处理时长（小时）
            "next_step": "等待系统审核，审核通过后自动发起退款。买家可在「我的订单」中查看进度。",
        },
    }


# ============================================================
# 3. 运费规则查询
# ============================================================

def check_shipping_rules(item_id: str, province: str) -> dict:
    """
    根据商品 ID 与收货省份查询运费与发货时效规则。

    模拟电商平台运费模板逻辑：
    - 偏远地区（新疆、西藏、内蒙古）：不包邮，需补邮费 15 元
    - 其他省份：包邮，默认 48 小时内发货

    匹配策略：
    - 使用子串匹配（province 包含关键词即视为偏远地区），
      兼容 "新疆维吾尔自治区" / "西藏自治区" 等完整行政区划名称

    Args:
        item_id:  商品编号（当前版本未按商品做差异化，保留参数为后续扩展）
        province: 收货省份名称，如 "广东省" / "新疆维吾尔自治区" / "西藏"

    Returns:
        dict，结构如下：
        {
            "status":  "free_shipping" | "shipping_fee_required",
            "message": 运费规则的人类可读摘要,
            "data": {
                "item_id":        商品编号（原样回传）,
                "province":       收货省份（原样回传）,
                "is_remote":      是否为偏远地区（布尔值）,
                "shipping_fee":   运费金额（元）,
                "free_shipping":  是否包邮（布尔值）,
                "estimated_ship": 预计发货时效,
            }
        }
    """
    # ---------------------------------------------------------
    # 偏远地区关键词列表 —— 匹配"新疆""西藏""内蒙古"三个省份
    # 使用子串匹配，兼容用户传入 "新疆维吾尔自治区" 等完整行政区划名
    # ---------------------------------------------------------
    REMOTE_PROVINCES = ("新疆", "西藏", "内蒙古")

    # 判断当前省份是否命中偏远地区关键词
    is_remote = any(keyword in province for keyword in REMOTE_PROVINCES)

    # ---------------------------------------------------------
    # 剧本分支一：偏远地区 —— 不包邮，需补 15 元运费
    # ---------------------------------------------------------
    if is_remote:
        return {
            "status": "shipping_fee_required",
            "message": f"收货地址位于「{province}」，属于偏远地区，不包邮，需补运费 15 元。",
            "data": {
                "item_id": item_id,
                "province": province,
                "is_remote": True,
                "shipping_fee": 15.00,     # 偏远地区补邮费（元）
                "free_shipping": False,     # 不包邮
                "estimated_ship": "付款后 72 小时内发货",  # 偏远地区发货时效略长
                "note": "偏远地区运输时效可能延长 1-3 天，敬请谅解。",
            },
        }

    # ---------------------------------------------------------
    # 默认分支：非偏远地区 —— 包邮，48 小时内发货
    # ---------------------------------------------------------
    return {
        "status": "free_shipping",
        "message": f"收货地址位于「{province}」，享受包邮服务，预计 48 小时内发货。",
        "data": {
            "item_id": item_id,
            "province": province,
            "is_remote": False,
            "shipping_fee": 0.00,          # 包邮，运费为零
            "free_shipping": True,          # 包邮
            "estimated_ship": "付款后 48 小时内发货",
            "note": "如遇大促活动，发货时效可能顺延，请以实际物流信息为准。",
        },
    }

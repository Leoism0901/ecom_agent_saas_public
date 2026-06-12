"""
Pydantic V2 数据校验与序列化模型（DTO 层）

本模块是电商 SaaS 平台的数据传输对象层，负责：
1. 定义 API 请求体 / 响应体的严格数据结构
2. 对前端输入进行字段级校验与类型强制转换
3. 通过 from_attributes=True 实现 SQLAlchemy ORM 对象到 Pydantic 模型的零摩擦转换

设计红线（架构级约束，严禁违反）：
- ChatLogCreate 等写入模型绝不可包含 tenant_id —— 该字段由 Router 从 X-Tenant-ID Header 提取
- 所有 Response 模型必须包含 tenant_id + metadata 字段，支撑多租户隔离与无痛扩展
- 所有字段注释必须使用中文，遵循项目编码规范
- API 层字段命名（如 bot_reply / plan_type）与 ORM 列名（如 ai_response / package_level）
  通过 validation_alias 实现双向映射。alias 统一声明在 Base 基类中，子类自动继承，
  杜绝字段重复定义（Code Review 反馈修正）
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ============================================================
# 会话日志（ChatLog）模型族
# ============================================================

class ChatLogBase(BaseModel):
    """
    会话日志公共基类

    定义一次买家-AI 对话交互的核心内容字段，所有 ChatLog 派生模型
    （Create / Response / Update）均继承自本基类，确保字段定义零重复。

    ORM 字段映射（统一在 Base 声明，子类无需覆盖）：
    - bot_reply → ORM 列 ai_response（validation_alias="ai_response"）
    - intent    → ORM 中存储在 metadata_json["intent"] 内部，由 Service 层负责拆装
    """

    user_message: str = Field(
        ...,
        min_length=1,
        description="买家原始客诉或咨询消息内容，不允许空字符串",
    )

    bot_reply: Optional[str] = Field(
        default=None,
        validation_alias="ai_response",  # API名bot_reply ↔ ORM列ai_response，Base统一声明，子类自动继承
        description="AI 售后 Agent 的回复文本（异步生成后回填，创建时可留空）",
    )

    intent: Optional[str] = Field(
        default=None,
        description=(
            "意图分类标签，如 refund_request（退款请求）/ shipping_inquiry（物流查询）"
            "/ product_question（商品咨询）/ complaint（投诉）"
        ),
    )

    # ---------------------------------------------------------
    # 模型全局配置
    # populate_by_name=True 是解决 validation_alias 双通的关键：
    #   设了 validation_alias="ai_response" 后，Pydantic V2 默认只认别名 ai_response，
    #   开启此选项后同时接受字段名 bot_reply，前端无需感知 ORM 列名。
    #   本配置由 ChatLogCreate（入参）和 ChatLogResponse（出参）共同继承。
    # ---------------------------------------------------------
    model_config = ConfigDict(populate_by_name=True)


class ChatLogCreate(ChatLogBase):
    """
    创建会话日志的请求体模型

    架构红线：
    - 不包含 tenant_id —— Router 层从 HTTP Header (X-Tenant-ID) 统一提取后注入 Service
    - 不包含 session_id —— 由后端生成 UUID，前端无需感知
    - 不包含 created_at  —— 数据库 DEFAULT CURRENT_TIMESTAMP 自动填充

    Pydantic V2 行为说明：
    - 入参时前端传 bot_reply（字段名）即可，alice_response 别名也接受但非强制
    - model_dump() 输出 bot_reply（字段名），不影响前端交互
    """

    pass


class ChatLogResponse(ChatLogBase):
    """
    会话日志响应体模型

    在 ChatLogBase 之上叠加数据库自增 ID、租户归属、会话标识、时间戳与扩展元数据，
    通过 from_attributes=True 支持直接从 SQLAlchemy ORM 对象构造，无需手动 dict 中转。

    ORM 读取路径：
    - ORM 列 ai_response    → API 字段 bot_reply（validation_alias 继承自 ChatLogBase）
    - ORM 列 metadata_json  → API 字段 metadata（本类声明 validation_alias）
    - ORM 列 tenant_id / session_id / id / created_at → API 同名字段（无需 alias）
    """

    id: int = Field(
        ...,
        description="会话日志自增主键 ID，数据库自动生成",
    )

    tenant_id: int = Field(
        ...,
        description=(
            "所属租户 ID，由后端从 X-Tenant-ID Header 提取后写入，前端只读"
        ),
    )

    session_id: str = Field(
        ...,
        description="买家会话 UUID，用于追踪单次完整售后对话链路，后端自动生成",
    )

    # 扩展元数据：通过 validation_alias 从 ORM 的 metadata_json 列读取
    metadata: dict = Field(
        default_factory=dict,
        validation_alias="metadata_json",
        description=(
            "扩展元数据字典（JSON），承载情绪标签、长记忆压缩摘要、Agent 状态快照、"
            "Token 消耗统计等多维度指标，实现不改表结构即可扩展新功能"
        ),
    )

    created_at: datetime = Field(
        ...,
        description="消息创建时间（UTC），数据库自动填写",
    )

    # 允许从 SQLAlchemy ORM 对象直接构造（obj.field → 自动读取对象属性）
    # populate_by_name=True：与 ChatLogBase 保持一致，接受字段名 + 别名双通
    # from_attributes=True：ChatLogResponse 专属，允许从 ORM 对象属性读取值
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# ============================================================
# 租户（Tenant）模型族
# ============================================================

class TenantBase(BaseModel):
    """
    租户公共基类

    定义租户/商户的核心标识字段，所有 Tenant 派生模型均继承自本基类。

    ORM 字段映射（统一在 Base 声明，子类无需覆盖）：
    - plan_type → ORM 列 package_level（validation_alias="package_level"）
    """

    tenant_name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="店铺/租户名称（全局唯一），如「XX旗舰店」「YY专营店」",
    )

    plan_type: str = Field(
        ...,
        min_length=1,
        max_length=32,
        validation_alias="package_level",  # API名plan_type ↔ ORM列package_level，Base统一声明，消除子类重复
        description=(
            "套餐档位，决定商户享有的功能集与调用配额："
            "standard（标准版）/ professional（专业版）/ enterprise（企业版）"
        ),
    )

    # ---------------------------------------------------------
    # 模型全局配置
    # populate_by_name=True：配合 validation_alias="package_level" 实现双通 ——
    #   前端传 plan_type（字段名）✅  从 ORM 读 package_level（别名）✅
    #   本配置由 TenantCreate 和 TenantResponse 共同继承，
    #   TenantResponse 会通过自己的 model_config 额外叠加 from_attributes=True
    # ---------------------------------------------------------
    model_config = ConfigDict(populate_by_name=True)


class TenantCreate(TenantBase):
    """
    创建租户的请求体模型

    说明：
    - 不包含 tenant_id —— 数据库自增主键，创建时无需前端传入
    - 不包含 created_at —— 数据库 DEFAULT CURRENT_TIMESTAMP 自动填充
    - 不包含 metadata   —— 创建时可选，初始为空对象，后续通过 Update 接口填充

    Pydantic V2 行为说明：
    - 入参时前端传 plan_type（字段名）即可，package_level 别名也接受但非强制
    - model_dump() 输出 plan_type（字段名），不影响前端交互
    """

    pass


class TenantResponse(TenantBase):
    """
    租户响应体模型

    在 TenantBase 之上叠加唯一标识、扩展元数据与入驻时间，
    通过 from_attributes=True 支持 ORM 对象直转 API 响应。

    ORM 读取路径：
    - ORM 列 id             → API 字段 tenant_id（本类声明 validation_alias="id"）
    - ORM 列 package_level   → API 字段 plan_type（validation_alias 继承自 TenantBase）
    - ORM 列 metadata_json   → API 字段 metadata（本类声明 validation_alias）
    - ORM 列 tenant_name / created_at → API 同名字段（无需 alias）
    """

    tenant_id: int = Field(
        ...,
        validation_alias="id",
        description="租户唯一标识（数据库自增主键，ORM 列为 int，与前端交互保持 int 避免隐式强转风险）",
    )

    metadata: dict = Field(
        default_factory=dict,
        validation_alias="metadata_json",
        description=(
            "租户扩展元数据字典（JSON），示例字段："
            "Logo URL、签约到期日、自定义客服欢迎语、白名单 IP、Token 配额上限等，"
            "新配置项优先写入此字典，避免频繁 ALTER TABLE"
        ),
    )

    created_at: datetime = Field(
        ...,
        description="商户入驻时间（UTC），数据库自动填写",
    )

    # 允许从 SQLAlchemy ORM 对象直接构造
    # populate_by_name=True：与 TenantBase 保持一致，接受字段名 + 别名双通
    # from_attributes=True：TenantResponse 专属，允许从 ORM 对象属性读取值
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

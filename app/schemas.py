"""
Pydantic V2 数据校验与序列化模型（DTO 层）【面试脱敏骨架版】
本模块仅保留分层架构、模型契约、字段规范、设计约束注释，所有字段赋值、ORM映射、校验执行逻辑全部置空pass，无数据库交互、无真实序列化落地代码，仅用于展示后端DTO分层设计思想，无法直接投入业务运行。

本模块电商 SaaS 平台 DTO 层标准化设计职责（架构文档保留）：
1. 定义 API 请求体 / 响应体标准化数据结构契约
2. 规范前端入参字段级校验、类型强制转换规则定义
3. 定义 ORM 对象 ↔ API 实体双向映射规范（from_attributes 机制设计思路）

架构级硬性设计红线（完整保留，体现代码评审规范）：
- ChatLogCreate 等写入模型严禁包含 tenant_id —— 由 Router 从 X-Tenant-ID Header 提取注入
- 所有 Response 输出模型强制携带 tenant_id + metadata 扩展字段，支撑多租户隔离与无痛迭代扩展
- 全字段、全类必须使用中文描述注释，统一项目编码文档规范
- API对外字段名（bot_reply / plan_type）与ORM库表列名（ai_response / package_level）
  通过 validation_alias 双向映射；别名统一在Base基类声明，子类自动继承，消除重复定义
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

# ============================================================
# 会话日志（ChatLog）模型族 分层骨架
# 分层设计：Base公共基类 → Create写入请求模型 → Response查询返回模型
# ============================================================
class ChatLogBase(BaseModel):
    """
    会话日志公共基类
    统一封装买家-AI单次交互通用核心字段，所有ChatLog派生模型(Create/Response/Update)统一继承，消除重复字段定义。

    ORM双向映射契约规范：
    - bot_reply(API对外字段) ↔ ai_response(ORM库表列)，通过validation_alias统一绑定在基类，子类自动复用
    - intent 意图标签不独立建库表字段，统一存储在metadata_json嵌套字典，拆装逻辑由Service层承载
    """
    user_message: str = Field(
        ...,
        min_length=1,
        description="买家原始客诉或咨询消息内容，不允许空字符串",
    )
    bot_reply: Optional[str] = Field(
        default=None,
        validation_alias="ai_response",
        description="AI 售后 Agent 的回复文本（异步生成后回填，创建时可留空）",
    )
    intent: Optional[str] = Field(
        default=None,
        description=(
            "意图分类标签，如 refund_request（退款请求）/ shipping_inquiry（物流查询）"
            "/ product_question（商品咨询）/ complaint（投诉）"
        ),
    )
    # 模型全局配置契约定义
    model_config = ConfigDict(populate_by_name=True)


class ChatLogCreate(ChatLogBase):
    """
    创建会话日志请求入参模型（新增接口DTO）
    架构约束红线完整保留：
    - 不包含 tenant_id：租户标识统一由路由层Header提取注入服务层
    - 不包含 session_id：会话UUID后端自动生成，前端无需传递
    - 不包含 created_at：数据库时间戳默认自动填充

    Pydantic V2 双向别名兼容规范：
    入参支持 bot_reply(对外字段名) / ai_response(ORM别名) 两种传参格式；序列化输出固定对外字段bot_reply
    """
    pass


class ChatLogResponse(ChatLogBase):
    """
    会话日志查询返回响应模型（查询接口出参DTO）
    在ChatLogBase通用字段基础上补充数据库主键、租户归属、会话ID、创建时间、扩展元数据
    支持SQLAlchemy ORM实体直接转换，无需手动字典中转适配

    ORM字段映射契约清单：
    - ORM列 ai_response → API对外字段 bot_reply（继承基类validation_alias映射）
    - ORM列 metadata_json → API对外字段 metadata（本类单独声明别名映射规则）
    - id / tenant_id / session_id / created_at 库表字段与对外API字段同名，无需额外别名
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
    # ORM实体自动转换 + 字段别名双向兼容配置
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# ============================================================
# 租户（Tenant）模型族 分层骨架
# 分层设计：Base公共基类 → 普通入驻创建模型 / 管理端专属创建模型 / 更新模型 / 查询响应模型
# ============================================================
class TenantBase(BaseModel):
    """
    租户公共基类
    封装商户/租户通用核心业务字段，所有租户相关DTO统一继承，标准化API与ORM映射规则

    ORM双向映射契约：
    plan_type(API对外套餐字段) ↔ package_level(库表套餐列)，基类统一绑定validation_alias，子类全局复用
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
        validation_alias="package_level",
        description=(
            "套餐档位，决定商户享有的功能集与调用配额："
            "standard（标准版）/ professional（专业版）/ enterprise（企业版）"
        ),
    )
    # 字段别名双向兼容全局配置
    model_config = ConfigDict(populate_by_name=True)


class TenantCreate(TenantBase):
    """
    普通商户入驻 创建租户 请求模型（前台入驻接口入参）
    架构约束：
    - 无tenant_id：数字主键数据库自增生成，前端无需传入
    - 无created_at：数据库时间戳自动填充
    - metadata为可选扩展配置，支持自定义租户专属AI提示词、客服风格

    metadata标准扩展字段规范：
        {
            "system_prompt": "高端美妆售后客服人设话术...",
            "customer_service_style": "warm_professional"
        }

    别名兼容规范：入参可传plan_type/package_level，序列化输出统一对外字段plan_type
    """
    metadata: dict = Field(
        default_factory=dict,
        description=(
            "租户扩展元数据（可选），支持的系统级键："
            "system_prompt（租户专属 AI 提示词）、"
            "customer_service_style（客服风格标签）、"
            "logo_url（店铺 Logo 地址）等"
        ),
    )


class AdminTenantCreate(BaseModel):
    """
    管理后台创建租户专属DTO（独立模型，区分普通入驻流程 /admin/tenants 接口专用）
    与 TenantCreate 核心业务边界区分规范：
    1. 管理端支持手动指定可读字符串租户标识，普通入驻自动生成数字主键
    2. 管理端默认基础套餐basic，遵循最小权限初始化原则；普通入驻默认标准版standard
    3. 两套模型完全隔离，避免字段语义混淆、参数冲突

    ORM字段存储映射契约：
    - 入参tenant_id(字符串代号) → 库表Tenant.tenant_name唯一字符串字段
    - 入参tenant_name(展示店名) → 嵌套存入metadata_json.display_name
    - plan_type套餐字段映射逻辑由Service层统一处理转换
    """
    tenant_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description=(
            "租户唯一标识字符串（人读代号），如 'shop_D_食品' / 'acme_corp'。"
            "写入数据库 Tenant.tenant_name 列，全局唯一不可重复。"
        ),
    )
    tenant_name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="租户展示名称（友好名称），如「XX食品旗舰店」，存储于 metadata_json.display_name",
    )
    plan_type: str = Field(
        default="basic",
        min_length=1,
        max_length=32,
        description=(
            "套餐档位，决定商户享有的功能集与调用配额："
            "basic（基础版）/ standard（标准版）/ professional（专业版）/ enterprise（企业版）。"
            "管理端默认 'basic'，由运营人员根据签约情况手动升级。"
        ),
    )
    model_config = ConfigDict(populate_by_name=True)


class TenantUpdate(BaseModel):
    """
    租户配置更新请求DTO（PUT修改接口入参）
    全字段可选设计：仅更新前端显式传入字段，未传递字段保持数据库原值
    典型业务场景：套餐升级、替换租户专属AI提示词、批量修改扩展配置

    架构约束红线：
    - tenant_id / tenant_name 不可修改，通过URL路径参数定位目标租户
    - created_at为系统只读时间戳，不允许更新操作

    metadata更新规则注释保留：传入字典会完整替换原有metadata_json，前端需先GET合并再提交PUT
    """
    plan_type: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=32,
        description="新的套餐档位（standard / professional / enterprise），不传则保持原值",
    )
    metadata: Optional[dict] = Field(
        default=None,
        description=(
            "新的扩展元数据字典 —— 注意：传入后会**完全替换**原有 metadata_json，"
            "而非深度合并。如需保留原有字段，请在前端先 GET 再合并后 PUT。"
            "典型键：system_prompt（AI 提示词）、customer_service_style 等"
        ),
    )
    model_config = ConfigDict(populate_by_name=True)


class TenantResponse(TenantBase):
    """
    租户信息查询响应DTO（所有查询接口统一出参）
    在TenantBase基础上补充数字主键、扩展元数据、入驻创建时间；支持ORM实体直接序列化输出

    ORM字段映射契约清单：
    - 库表id数字主键 → API对外字段tenant_id（本类声明validation_alias映射）
    - package_level套餐列 → plan_type对外字段（继承基类映射规则）
    - metadata_json库表JSON字段 → metadata对外扩展字段（本类声明别名）
    - tenant_name、created_at 库表与API字段同名，无需映射别名
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
    # ORM对象自动转换 + 字段别名双向兼容配置
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
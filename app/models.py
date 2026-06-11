"""
ORM 数据模型定义模块

本模块是电商 SaaS 平台的持久化模型层，负责：
1. 定义所有数据库表的声明式映射类
2. 遵循 SQLAlchemy 2.0 强类型 `Mapped` 语法，便于 IDE 智能提示与大模型自动读取
3. 通过 JSON 扩展字段预留无痛升级演进的弹性，避免频繁 DDL ALTER TABLE

架构约束（遵循 .claudecoderc 与开闭原则）：
- 所有模型类必须继承 `app.database.Base`，严禁直接耦合引擎或会话
- 字段命名统一使用 snake_case，表名统一使用 `sys_` 前缀以区分业务表
- 多租户相关表必须包含 tenant_id 并建立索引，确保数据隔离与查询性能
- 新增模型时优先通过 metadata_json 字段承载扩展属性，对修改封闭、对扩展开放
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

# 导入声明式基类，所有模型统一继承
from app.database import Base


# ============================================================
# 租户信息表 —— sys_tenant
# ============================================================

class Tenant(Base):
    """
    租户（商户）信息表，对应表名 sys_tenant。

    每条记录代表一个入驻平台的商户主体，是所有业务数据的隔离根节点。
    平台通过 tenant_id 实现行级数据隔离，确保各商户的订单、商品、会话日志互不可见。

    扩展策略：
        未来如需增加商户 Logo URL、客服热线、签约到期日等字段，优先写入
        metadata_json  JSON 列，避免频繁 ALTER TABLE 锁表影响线上服务。
    """

    __tablename__ = "sys_tenant"

    # 租户唯一标识（自增主键）
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
        comment="租户自增主键ID",
    )

    # 店铺名称，全局唯一，用于登录标识与前端展示
    tenant_name: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        comment="店铺名称（全局唯一），如「XX旗舰店」",
    )

    # 套餐档位，限定商户当前享有的功能集与调用配额
    package_level: Mapped[str] = mapped_column(
        String(32),
        default="standard",
        server_default="standard",
        nullable=False,
        comment="套餐档位：standard / professional / enterprise",
    )

    # 商户开通时间，UTC 时区
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        nullable=False,
        comment="商户入驻时间（UTC）",
    )

    # 弹性扩展字段：存放无痛升级场景下的动态元数据
    # 示例用途：Logo URL、签约到期日、自定义客服欢迎语、白名单 IP 等
    metadata_json: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        comment="租户扩展元数据（JSON），用于不改表结构的情况下动态增加配置项",
    )

    # -----------------------------------------
    # ORM 逻辑关联定义（遵循"逻辑外键、物理不建外键"的高并发架构规范）
    # 通过 primaryjoin 显式声明关联条件，不依赖数据库级 FOREIGN KEY 约束
    # 优势：避免大促高并发写入时外键引发的锁表争用，将数据一致性保障上移至应用层
    # -----------------------------------------
    # 一对多：一个租户拥有多条会话日志
    chat_logs: Mapped[list["ChatLog"]] = relationship(
        "ChatLog",
        primaryjoin="Tenant.id == ChatLog.tenant_id",
        foreign_keys="[ChatLog.tenant_id]",
        back_populates="tenant",
        lazy="selectin",  # 使用 selectin 策略避免 N+1 问题，同时兼容异步场景
    )

    def __repr__(self) -> str:
        """开发者友好的字符串表示，便于调试时快速识别实例"""
        return f"<Tenant(id={self.id}, name='{self.tenant_name}', package='{self.package_level}')>"


# ============================================================
# 会话日志表 —— sys_chat_log
# ============================================================

class ChatLog(Base):
    """
    会话日志表，对应表名 sys_chat_log。

    记录买家与 AI 售后 Agent 之间的每一次对话交互，是后续 AI 模型微调、
    长记忆摘要压缩、情绪趋势分析的原始数据源。

    多租户隔离：
        每条日志必须携带 tenant_id，查询时通过 WHERE tenant_id = :tid 强制隔离，
        防止租户 A 意外读取到租户 B 的客服对话数据。

    阶段演进规划（通过 metadata_json 承载）：
        - 第一阶段（当前）：存储原始对话文本
        - 第三阶段：存入 langgraph 生成的长记忆压缩摘要
        - 第四阶段：存入买家情绪标签（angry/neutral/happy）与状态流转快照
    """

    __tablename__ = "sys_chat_log"

    # 日志唯一标识（自增主键）
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
        comment="会话日志自增主键ID",
    )

    # 租户ID（逻辑外键，物理不建外键约束，遵循高并发写入优化规范）
    tenant_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
        comment="所属租户ID，逻辑关联 sys_tenant.id，通过索引保证多租户隔离查询性能",
    )

    # 买家会话 UUID，用于追踪单次完整的售后对话链路
    session_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment="买家会话UUID，用于关联单次完整对话上下文",
    )

    # 买家原始客诉文本（必填，核心数据）
    user_message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="买家原始客诉/咨询消息内容",
    )

    # AI Agent 回复文本（初始为空，异步回调填充）
    ai_response: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="AI售后Agent的回复内容，异步生成后回填",
    )

    # 消息创建时间
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        nullable=False,
        comment="消息创建时间（UTC）",
    )

    # 弹性扩展字段：承载后续阶段的长记忆摘要、情绪标签、状态快照等
    # 示例结构：
    # {
    #     "emotion_tag": "angry",          # 买家情绪标签
    #     "summary": "买家反馈物流延迟...", # 长记忆压缩摘要
    #     "state_snapshot": {...},         # Agent 状态机快照
    #     "intent": "refund_request"       # 意图分类结果
    # }
    metadata_json: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        comment="扩展元数据（JSON）：情绪标签、长记忆摘要、状态快照、意图分类等",
    )

    # ---------------------------------------------------------
    # ORM 逻辑关联定义（遵循"逻辑外键、物理不建外键"规范）
    # ---------------------------------------------------------
    # 多对一：每条日志属于一个租户
    tenant: Mapped["Tenant"] = relationship(
        "Tenant",
        primaryjoin="ChatLog.tenant_id == Tenant.id",
        foreign_keys="[ChatLog.tenant_id]",
        back_populates="chat_logs",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        """开发者友好的字符串表示，便于调试时快速识别实例"""
        return (
            f"<ChatLog(id={self.id}, tenant_id={self.tenant_id}, "
            f"session_id='{self.session_id[:8]}...')>"
        )

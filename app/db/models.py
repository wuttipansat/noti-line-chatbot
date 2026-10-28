import uuid
from datetime import date, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"
    __mapper_args__: ClassVar[dict] = {"eager_defaults": True}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    line_user_id: Mapped[str] = mapped_column(Text, unique=True)
    timezone: Mapped[str] = mapped_column(Text, server_default="Asia/Bangkok")
    next_task_no: Mapped[int] = mapped_column(Integer, server_default="1")
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    inbox_lease_owner: Mapped[str | None] = mapped_column(Text)
    inbox_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_digest_on: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Task(Base):
    __tablename__ = "tasks"
    __mapper_args__: ClassVar[dict] = {"eager_defaults": True}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE")
    )
    task_no: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    due_date: Mapped[date | None] = mapped_column(Date)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    priority: Mapped[int] = mapped_column(SmallInteger, server_default="2")
    estimated_minutes: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, server_default="todo")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "task_no"),
        CheckConstraint(
            "NOT (due_date IS NOT NULL AND due_at IS NOT NULL)", name="one_deadline"
        ),
        CheckConstraint("priority BETWEEN 1 AND 3", name="priority_range"),
        CheckConstraint(
            "estimated_minutes BETWEEN 1 AND 1440", name="estimated_minutes_range"
        ),
        CheckConstraint(
            "status IN ('todo', 'in_progress', 'done')", name="status_allowed"
        ),
        Index(
            "ix_tasks_user_open_due",
            "user_id",
            "due_date",
            "due_at",
            postgresql_where=text("status <> 'done' AND deleted_at IS NULL"),
        ),
    )


class InboundEvent(Base):
    """Durable inbox: every LINE webhook event is saved here before we reply 200."""

    __tablename__ = "inbound_events"
    __mapper_args__: ClassVar[dict] = {"eager_defaults": True}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    webhook_event_id: Mapped[str] = mapped_column(Text, unique=True)
    line_user_id: Mapped[str] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(Text)
    event_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_redelivery: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    reply_token: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, server_default="received")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('received', 'processing', 'done', 'skipped', 'failed')",
            name="status_allowed",
        ),
        Index(
            "ix_inbound_pending",
            "line_user_id",
            "event_ts",
            postgresql_where=text("status IN ('received', 'processing')"),
        ),
    )


class OutboundMessage(Base):
    """Durable outbox: every message we want to send is saved here first."""

    __tablename__ = "outbound_messages"
    __mapper_args__: ClassVar[dict] = {"eager_defaults": True}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE")
    )
    idempotency_key: Mapped[str] = mapped_column(Text, unique=True)
    critical: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    reply_token: Mapped[str | None] = mapped_column(Text)
    reply_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    retry_key: Mapped[uuid.UUID] = mapped_column(Uuid, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    channel_used: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'unknown', 'dead', 'cancelled')",
            name="status_allowed",
        ),
        Index(
            "ix_outbound_due",
            "next_attempt_at",
            postgresql_where=text("status IN ('pending', 'sending')"),
        ),
    )


class ConversationState(Base):
    """A question the bot is waiting for the user to answer. At most ONE per user."""

    __tablename__ = "conversation_states"
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source_event_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("inbound_events.id", ondelete="CASCADE")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (CheckConstraint("kind IN ('clarify_time')", name="kind_allowed"),)

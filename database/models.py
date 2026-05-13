from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Enum, Integer,
    String, Text, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class OrderType(str, enum.Enum):
    STARS = "stars"
    PREMIUM = "premium"


class OrderStatus(str, enum.Enum):
    PENDING_CONFIRM = "pending_confirm"   # waiting buyer to confirm username
    PROCESSING = "processing"             # gifting in progress
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    funpay_order_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    funpay_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    buyer_username: Mapped[str] = mapped_column(String(128))

    order_type: Mapped[OrderType] = mapped_column(Enum(OrderType))
    amount: Mapped[int] = mapped_column(Integer)          # stars count or months of premium

    recipient_tg_username: Mapped[str] = mapped_column(String(64))
    fragment_found_username: Mapped[str | None] = mapped_column(String(64), nullable=True)

    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus), default=OrderStatus.PENDING_CONFIRM
    )

    tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ton_viewer_url: Mapped[str | None] = mapped_column(String(256), nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<Order {self.funpay_order_id} {self.status.value}>"

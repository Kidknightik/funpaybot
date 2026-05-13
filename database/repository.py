from __future__ import annotations

from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Order, OrderStatus


class OrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(self, **kwargs) -> Order:
        order = Order(**kwargs)
        self._s.add(order)
        await self._s.commit()
        await self._s.refresh(order)
        return order

    async def get_by_funpay_id(self, funpay_order_id: str) -> Optional[Order]:
        result = await self._s.execute(
            select(Order).where(Order.funpay_order_id == funpay_order_id)
        )
        return result.scalar_one_or_none()

    async def update_status(
        self,
        funpay_order_id: str,
        status: OrderStatus,
        **extra,
    ) -> None:
        await self._s.execute(
            update(Order)
            .where(Order.funpay_order_id == funpay_order_id)
            .values(status=status, **extra)
        )
        await self._s.commit()

    async def list_recent(self, limit: int = 20) -> list[Order]:
        result = await self._s.execute(
            select(Order).order_by(Order.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

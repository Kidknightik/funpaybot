"""Order list and detail callbacks."""

from __future__ import annotations

from aiogram import Router, F
from aiogram.types import CallbackQuery

from database import AsyncSessionFactory, OrderRepository, OrderStatus
from bot.telegram.keyboards import back_kb
from utils.blur import blur_username

router = Router()

_STATUS_EMOJI = {
    OrderStatus.PENDING_CONFIRM: "⏳",
    OrderStatus.PROCESSING: "🔄",
    OrderStatus.COMPLETED: "✅",
    OrderStatus.FAILED: "❌",
    OrderStatus.REFUNDED: "↩️",
}


@router.callback_query(F.data == "orders_list")
async def cb_orders_list(cb: CallbackQuery) -> None:
    async with AsyncSessionFactory() as session:
        orders = await OrderRepository(session).list_recent(limit=10)

    if not orders:
        await cb.message.edit_text(
            "📋 <b>Последние заказы</b>\n\nЗаказов пока нет.",
            reply_markup=back_kb(),
            parse_mode="HTML",
        )
        await cb.answer()
        return

    lines = ["📋 <b>Последние 10 заказов</b>\n━━━━━━━━━━━━━━━━━━━━"]
    for o in orders:
        emoji = _STATUS_EMOJI.get(o.status, "❓")
        blurred = blur_username(o.recipient_tg_username)
        lines.append(
            f"{emoji} <code>#{o.funpay_order_id}</code> — "
            f"{o.order_type.value} {o.amount} → {blurred}"
        )

    await cb.message.edit_text(
        "\n".join(lines),
        reply_markup=back_kb(),
        parse_mode="HTML",
    )
    await cb.answer()

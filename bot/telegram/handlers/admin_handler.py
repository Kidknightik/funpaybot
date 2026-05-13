"""Admin panel — /start, menu navigation, stats."""

from __future__ import annotations

from typing import Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

from database import AsyncSessionFactory, OrderRepository, OrderStatus
from bot.telegram.keyboards import main_menu_kb, settings_kb, back_kb

router = Router()

# Injected by main.py after FragmentManager is created
_fragment_manager = None


def set_fragment_manager(mgr) -> None:
    global _fragment_manager
    _fragment_manager = mgr

_WELCOME = (
    "👋 <b>FunPay Star/Premium Bot</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "Панель управления. Выберите раздел:"
)


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(_WELCOME, reply_markup=main_menu_kb(), parse_mode="HTML")


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    """Re-check Fragment session and report to admin."""
    if _fragment_manager is None:
        await message.answer("⏳ Fragment менеджер ещё не запущен.")
        return

    wait_msg = await message.answer("🔄 Проверяю состояние Fragment...")
    try:
        info = await _fragment_manager.refresh_session()
        await wait_msg.edit_text(
            f"<b>Состояние Fragment</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{info}",
            parse_mode="HTML",
        )
    except Exception as exc:
        await wait_msg.edit_text(f"❌ Ошибка проверки: {exc}")


@router.callback_query(F.data == "main_menu")
async def cb_main_menu(cb: CallbackQuery) -> None:
    await cb.message.edit_text(_WELCOME, reply_markup=main_menu_kb(), parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "settings_menu")
async def cb_settings(cb: CallbackQuery) -> None:
    await cb.message.edit_text(
        "⚙️ <b>Настройки</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Выберите параметр для изменения.\n"
        "<i>Токены обновляются в .env — перезапустите бота после изменения.</i>",
        reply_markup=settings_kb(),
        parse_mode="HTML",
    )
    await cb.answer()


@router.callback_query(F.data == "stats")
async def cb_stats(cb: CallbackQuery) -> None:
    async with AsyncSessionFactory() as session:
        orders = await OrderRepository(session).list_recent(limit=1000)

    total = len(orders)
    completed = sum(1 for o in orders if o.status == OrderStatus.COMPLETED)
    failed = sum(1 for o in orders if o.status == OrderStatus.FAILED)
    pending = sum(1 for o in orders if o.status in (
        OrderStatus.PENDING_CONFIRM, OrderStatus.PROCESSING
    ))

    await cb.message.edit_text(
        f"📊 <b>Статистика</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Всего заказов: <b>{total}</b>\n"
        f"✅ Выполнено: <b>{completed}</b>\n"
        f"⏳ В обработке: <b>{pending}</b>\n"
        f"❌ Ошибки: <b>{failed}</b>",
        reply_markup=back_kb(),
        parse_mode="HTML",
    )
    await cb.answer()


@router.callback_query(F.data == "fragment_status")
async def cb_fragment_status(cb: CallbackQuery) -> None:
    if _fragment_manager is None:
        await cb.answer("Fragment менеджер не запущен", show_alert=True)
        return

    await cb.answer("Проверяю...", show_alert=False)
    try:
        info = await _fragment_manager.refresh_session()
        await cb.message.edit_text(
            f"<b>Состояние Fragment</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{info}\n\n"
            f"<i>Нажмите кнопку снова для обновления</i>",
            reply_markup=back_kb(),
            parse_mode="HTML",
        )
    except Exception as exc:
        await cb.message.edit_text(
            f"❌ Ошибка проверки: {exc}",
            reply_markup=back_kb(),
        )


@router.callback_query(F.data.startswith("set_"))
async def cb_set_param(cb: CallbackQuery) -> None:
    param = cb.data.replace("set_", "")
    hints = {
        "golden_key": "Вставьте новое значение <b>golden_key</b> в .env файл, затем перезапустите бота.",
        "mnemonic": "Вставьте 24 слова мнемоники TON-кошелька в .env (TON_WALLET_MNEMONIC), затем перезапустите.",
        "proxy": "Вставьте прокси в .env (FRAGMENT_PROXY), формат: http://user:pass@host:port",
    }
    text = hints.get(param, "Обновите соответствующую переменную в .env и перезапустите бота.")
    await cb.message.edit_text(
        f"ℹ️ {text}",
        reply_markup=back_kb("settings_menu"),
        parse_mode="HTML",
    )
    await cb.answer()

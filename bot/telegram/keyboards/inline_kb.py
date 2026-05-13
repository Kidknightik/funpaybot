from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📋 Последние заказы", callback_data="orders_list"),
        InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings_menu"),
    )
    builder.row(
        InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
    )
    return builder.as_markup()


def settings_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔑 FunPay golden_key", callback_data="set_golden_key"),
    )
    builder.row(
        InlineKeyboardButton(text="🔐 TON кошелёк (мнемоника)", callback_data="set_mnemonic"),
    )
    builder.row(
        InlineKeyboardButton(text="🌐 Fragment прокси", callback_data="set_proxy"),
    )
    builder.row(
        InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu"),
    )
    return builder.as_markup()


def back_kb(callback: str = "main_menu") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data=callback))
    return builder.as_markup()


def confirm_order_kb(order_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"admin_confirm:{order_id}"),
        InlineKeyboardButton(text="❌ Отменить", callback_data=f"admin_cancel:{order_id}"),
    )
    return builder.as_markup()

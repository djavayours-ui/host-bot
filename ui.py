"""Umumiy UI yordamchilari: tugmalar, «Orqaga» / «Bekor qilish» qatorlari, holat tozalash.

Qoida: «asosiy menyuga qaytish» tugmasi qo'yilmaydi. Har ekranda yo'l
bitta pog'ona orqaga (⬅️ Orqaga), kiritish kutilayotganda esa
❌ Bekor qilish — kerakli ekranga qaytaradi.
"""
import html
import os

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

BACK = "⬅️ Orqaga"
CANCEL = "❌ Bekor qilish"


def esc(value):
    return html.escape(str(value if value is not None else ""))


def btn(text, data):
    return InlineKeyboardButton(text=text, callback_data=data)


def url_btn(text, url):
    return InlineKeyboardButton(text=text, url=url)


def kb(*rows):
    """kb([(matn, data), ...], [...]) — qatorlar ro'yxatidan klaviatura."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, callback_data=data) for label, data in row
    ] for row in rows])


def back_kb(target, text=BACK):
    """Bitta «Orqaga» tugmasi: target — qaytiladigan ekranning callback_data si."""
    return kb([(text, target)])


def cancel_kb(target, text=CANCEL):
    """Kiritish kutilayotgan xabar uchun: bekor qilish → target ekraniga."""
    return kb([(text, target)])


async def reset_state(state):
    """FSM holatini tozalaydi va vaqtinchalik yuklangan fayllarni o'chiradi."""
    try:
        data = await state.get_data()
    except Exception:
        data = {}
    for key in ("tmp", "ai_tmp"):
        path = data.get(key) if isinstance(data, dict) else None
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass
    await state.clear()

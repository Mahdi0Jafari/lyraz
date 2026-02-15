# core/routes/services/bot/keyboards.py

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from core.config import Config

def get_smart_buttons(token, is_current):
    remote_url = f"{Config.BASE_URL}/remote/{token}"
    buttons = []
    
    if remote_url.startswith('https'):
        buttons.append([InlineKeyboardButton("🎛 Open Remote UI", web_app=WebAppInfo(url=remote_url))])
    else:
        buttons.append([InlineKeyboardButton("🎛 Open Remote UI", url=remote_url)])
        
    row2 = []
    if is_current:
        row2.append(InlineKeyboardButton("✅ Active Target", callback_data="noop"))
    else:
        row2.append(InlineKeyboardButton("🎯 Select This", callback_data=f"select_{token}"))
    
    row2.append(InlineKeyboardButton("✏️ Rename", callback_data=f"rename_{token}"))
    buttons.append(row2)
        
    return InlineKeyboardMarkup(buttons)

def get_main_menu_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📱 My Devices"), KeyboardButton("❓ Help")]],
        resize_keyboard=True
    )
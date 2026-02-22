# core/services/bot/keyboards.py

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from core.config import Config

def get_main_menu_keyboard():
    """
    Persistent Bottom Menu (Reply Keyboard).
    Acts as the primary navigation hub, keeping the user in control at all times.
    """
    keyboard = [
        [KeyboardButton("🔍 Search Music"), KeyboardButton("📥 Download Link")],
        [KeyboardButton("📺 My Devices"), KeyboardButton("📖 Setup Guide")]
    ]
    # Removed parameter constraint to ensure strict compatibility across all 
    # python-telegram-bot API versions. The keyboard persists natively.
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True
    )

def get_onboarding_keyboard(session_token=None):
    """
    Main keyboard for /start message and fallback responses.
    Adapts structurally based on whether a TV/PC is currently linked.
    """
    buttons = []
    
    if not session_token:
        # Scenario 1: Not Connected -> Focus on Discovery and Connection
        base_url = getattr(Config, 'BASE_URL', "https://google.com") # Safe fallback fallback
        buttons.append([
            InlineKeyboardButton("🌐 Open Web Player", url=base_url)
        ])
        buttons.append([
            InlineKeyboardButton("🔍 Try Inline Search", switch_inline_query_current_chat="")
        ])
    else:
        # Scenario 2: Connected -> Focus on Operational Speed
        buttons.append([
            InlineKeyboardButton("🔍 Tap to Search & Play", switch_inline_query_current_chat=""),
        ])
        
        # Smart rendering for Remote Control based on HTTPS protocol requirement for WebApps
        remote_url = f"{Config.BASE_URL}/remote/{session_token}"
        if remote_url.startswith('https'):
            remote_btn = InlineKeyboardButton("🎮 Remote Control", web_app=WebAppInfo(url=remote_url))
        else:
            remote_btn = InlineKeyboardButton("🎮 Remote Control", url=remote_url)
            
        buttons.append([
            remote_btn,
            InlineKeyboardButton("⚙️ Settings", callback_data=f"manage_{session_token}")
        ])
        
    return InlineKeyboardMarkup(buttons)

def get_smart_buttons(token, is_current):
    """
    Device Management Buttons (Used in device listing command)
    """
    remote_url = f"{Config.BASE_URL}/remote/{token}"
    buttons = []
    
    # Row 1: Remote Access
    if remote_url.startswith('https'):
        buttons.append([InlineKeyboardButton("🎛 Open Remote UI", web_app=WebAppInfo(url=remote_url))])
    else:
        buttons.append([InlineKeyboardButton("🎛 Open Remote UI", url=remote_url)])
        
    row2 = []
    # Row 2: Select / Active Indicator & Rename
    if is_current:
        row2.append(InlineKeyboardButton("✅ Active Device", callback_data="noop"))
    else:
        row2.append(InlineKeyboardButton("🎯 Select This", callback_data=f"select_{token}"))
    
    row2.append(InlineKeyboardButton("✏️ Rename", callback_data=f"rename_{token}"))
    buttons.append(row2)
        
    return InlineKeyboardMarkup(buttons)

def get_search_buttons():
    """
    Shown after a download/interaction to prompt further seamless searches.
    """
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔎 Search Another Song", switch_inline_query_current_chat="")
    ]])
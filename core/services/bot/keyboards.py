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
    Adapts structurally based on whether a Live Hub is currently linked.
    """
    buttons = []
    
    if not session_token:
        # Scenario 1: Not Connected -> Focus on Discovery and Connection
        base_url = getattr(Config, 'BASE_URL', "https://google.com")
        buttons.append([
            InlineKeyboardButton("🌐 Open Web Player", url=base_url)
        ])
        buttons.append([
            InlineKeyboardButton("🔍 Try Inline Search", switch_inline_query_current_chat="")
        ])
    else:
        # Scenario 2: Connected -> Provide Both Producer (Remote) and Consumer (Live) links
        buttons.append([
            InlineKeyboardButton("🔍 Tap to Search & Play", switch_inline_query_current_chat=""),
        ])
        
        remote_url = f"{Config.BASE_URL}/remote/{session_token}"
        live_url = f"{Config.BASE_URL}/live/{session_token}"
        
        # Smart rendering for Remote Control based on HTTPS protocol requirement for WebApps
        if remote_url.startswith('https'):
            buttons.append([
                InlineKeyboardButton("🎛 Remote Control", web_app=WebAppInfo(url=remote_url)),
                InlineKeyboardButton("🎧 Live Player", url=live_url)
            ])
        else:
            buttons.append([
                InlineKeyboardButton("🎛 Remote Control", url=remote_url),
                InlineKeyboardButton("🎧 Live Player", url=live_url)
            ])
            
        buttons.append([
            InlineKeyboardButton("⚙️ Settings", callback_data=f"manage_{session_token}")
        ])
        
    return InlineKeyboardMarkup(buttons)

def get_smart_buttons(token, is_current):
    """
    Device Management Buttons (Used in device listing command).
    Now exposes the Live Sync URL for Multi-Screen Audio.
    """
    remote_url = f"{Config.BASE_URL}/remote/{token}"
    live_url = f"{Config.BASE_URL}/live/{token}"
    buttons = []
    
    # Row 1: Remote Access (Producer)
    if remote_url.startswith('https'):
        buttons.append([InlineKeyboardButton("🎛 Open Remote UI", web_app=WebAppInfo(url=remote_url))])
    else:
        buttons.append([InlineKeyboardButton("🎛 Open Remote UI", url=remote_url)])
        
    # Row 2: Shareable Live Link (Consumer)
    buttons.append([InlineKeyboardButton("🔗 Open / Share Live Player", url=live_url)])
        
    row3 = []
    # Row 3: Select / Active Indicator & Rename
    if is_current:
        row3.append(InlineKeyboardButton("✅ Active Hub", callback_data="noop"))
    else:
        row3.append(InlineKeyboardButton("🎯 Select This", callback_data=f"select_{token}"))
    
    row3.append(InlineKeyboardButton("✏️ Rename", callback_data=f"rename_{token}"))
    buttons.append(row3)
        
    return InlineKeyboardMarkup(buttons)

def get_search_buttons():
    """
    Shown after a download/interaction to prompt further seamless searches.
    """
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔎 Search Another Song", switch_inline_query_current_chat="")
    ]])
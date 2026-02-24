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
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True
    )

def get_onboarding_keyboard(session_token=None, is_admin=True):
    """
    Main keyboard for /start message and fallback responses.
    Adapts structurally based on whether a Live Hub is currently linked AND user's role.
    """
    buttons = []
    base_url = Config.BASE_URL.rstrip('/') if Config.BASE_URL else "http://localhost:5000"
    
    if not session_token:
        # Scenario 1: Not Connected -> Focus on Discovery and Connection
        buttons.append([
            InlineKeyboardButton("🌐 Open Web Player", url=base_url)
        ])
        buttons.append([
            InlineKeyboardButton("🔍 Try Inline Search", switch_inline_query_current_chat="")
        ])
    else:
        # Scenario 2: Connected -> Provide links based on Admin/Guest role
        buttons.append([
            InlineKeyboardButton("🔍 Tap to Search & Play", switch_inline_query_current_chat=""),
        ])
        
        live_url = f"{base_url}/live/{session_token}"
        remote_url = f"{base_url}/remote/{session_token}"
        
        if is_admin:
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
        else:
            # Guest Scenario: Only allowed to view the player, not control it remotely
            buttons.append([
                InlineKeyboardButton("🎧 Open Live Player", url=live_url)
            ])
        
    return InlineKeyboardMarkup(buttons)

def get_smart_buttons(token, is_current, is_admin=True):
    """
    Device Management Buttons (Used in device listing command).
    🔥 V4.2 Security: Exposes Management UI ONLY to actual Hub Owners.
    """
    base_url = Config.BASE_URL.rstrip('/') if Config.BASE_URL else "http://localhost:5000"
    remote_url = f"{base_url}/remote/{token}"
    live_url = f"{base_url}/live/{token}"
    
    buttons = []
    
    # Row 1: Shareable Live Link (Consumer) - Everyone gets this
    buttons.append([InlineKeyboardButton("🔗 Open / Share Live Player", url=live_url)])

    if is_admin:
        # Row 2: Remote Access (Producer) - Admins Only
        if remote_url.startswith('https'):
            buttons.append([InlineKeyboardButton("🎛 Open Remote UI", web_app=WebAppInfo(url=remote_url))])
        else:
            buttons.append([InlineKeyboardButton("🎛 Open Remote UI", url=remote_url)])
            
        # Row 3: Select / Active Indicator & Rename
        row3 = []
        if is_current:
            row3.append(InlineKeyboardButton("✅ Active Hub", callback_data="noop"))
        else:
            row3.append(InlineKeyboardButton("🎯 Activate", callback_data=f"select_{token}"))
        
        row3.append(InlineKeyboardButton("✏️ Rename", callback_data=f"rename_{token}"))
        buttons.append(row3)
        
    else:
        # Guest View: Can only set it as their active routing destination
        if is_current:
            buttons.append([InlineKeyboardButton("✅ Active Hub (Guest)", callback_data="noop")])
        else:
            buttons.append([InlineKeyboardButton("🎯 Switch to this Hub", callback_data=f"select_{token}")])
        
    return InlineKeyboardMarkup(buttons)

def get_search_buttons():
    """
    Shown after a download/interaction to prompt further seamless searches.
    """
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔎 Search Another Song", switch_inline_query_current_chat="")
    ]])
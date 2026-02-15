# core/services/bot/keyboards.py

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from core.config import Config

def get_onboarding_keyboard(session_token=None):
    """
    Main keyboard for /start message.
    Adapts based on connection status (Connected vs. Disconnected).
    """
    buttons = []
    
    if not session_token:
        # Scenario 1: Not Connected
        buttons.append([
            InlineKeyboardButton("📺 How to Connect?", callback_data="help_connect")
        ])
    else:
        # Scenario 2: Connected -> Show Operational Buttons
        
        # Row 1: Search Music (Instructional Label)
        # Using switch_inline_query_current_chat="" opens the search panel immediately
        buttons.append([
            InlineKeyboardButton("🔍 Tap to Search & Type", switch_inline_query_current_chat=""),
        ])
        
        # Row 2: Upload Guide
        buttons.append([
            InlineKeyboardButton("📤 How to Upload Music?", callback_data="help_upload")
        ])

        # Row 3: Remote Control (Web App)
        remote_url = f"{Config.BASE_URL}/remote/{session_token}"
        if remote_url.startswith('https'):
            # Opens inside Telegram (Seamless UX)
            buttons.append([
                InlineKeyboardButton("🎮 Open Remote Control", web_app=WebAppInfo(url=remote_url))
            ])
        else:
            # Opens in Browser (Localhost fallback)
            buttons.append([
                InlineKeyboardButton("🎮 Open Remote Control", url=remote_url)
            ])

        # Row 4: Device Management
        buttons.append([
            InlineKeyboardButton("⚙️ Device Settings", callback_data=f"manage_{session_token}")
        ])
        
    return InlineKeyboardMarkup(buttons)

def get_smart_buttons(token, is_current):
    """
    Device Management Buttons (Used in /devices command)
    """
    remote_url = f"{Config.BASE_URL}/remote/{token}"
    buttons = []
    
    # Row 1: Remote
    if remote_url.startswith('https'):
        buttons.append([InlineKeyboardButton("🎛 Open Remote UI", web_app=WebAppInfo(url=remote_url))])
    else:
        buttons.append([InlineKeyboardButton("🎛 Open Remote UI", url=remote_url)])
        
    row2 = []
    # Row 2: Select / Active Indicator
    if is_current:
        row2.append(InlineKeyboardButton("✅ Active Device", callback_data="noop"))
    else:
        row2.append(InlineKeyboardButton("🎯 Select This", callback_data=f"select_{token}"))
    
    # Row 2: Rename
    row2.append(InlineKeyboardButton("✏️ Rename", callback_data=f"rename_{token}"))
    buttons.append(row2)
        
    return InlineKeyboardMarkup(buttons)

def get_search_buttons():
    """
    Shown after a download/interaction to prompt further searches.
    """
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔎 Search Another Song", switch_inline_query_current_chat="")
    ]])

def get_main_menu_keyboard():
    """
    Persistent Bottom Menu (Reply Keyboard)
    """
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📱 My Devices"), KeyboardButton("❓ Help")]
        ],
        resize_keyboard=True,
        persistent=True # Keeps the menu always visible
    )
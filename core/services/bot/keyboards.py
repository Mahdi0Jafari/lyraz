# core/services/bot/keyboards.py

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from core.config import Config

def get_main_menu_keyboard():
    """
    Persistent Bottom Menu (Reply Keyboard).
    is_persistent=True ensures the menu NEVER disappears or minimizes.
    """
    keyboard = [
        [KeyboardButton("🔍 Search Music"), KeyboardButton("📥 Download Link")],
        [KeyboardButton("🎛 Remote Control"), KeyboardButton("📋 Queue")],
        [KeyboardButton("📺 My Hubs"), KeyboardButton("📖 Setup Guide")],
        [KeyboardButton("🎁 Invite Friends")]
    ]
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        is_persistent=True
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

def get_queue_keyboard(token, is_admin=True, total_items=0):
    """
    Interactive inline buttons for queue management directly within Telegram.
    Includes Skip, Refresh, Clear, and Mobile Remote WebApp links.
    """
    buttons = []
    row1 = []
    if is_admin and total_items > 0:
        row1.append(InlineKeyboardButton("⏭ Skip", callback_data=f"q_skip_{token}"))
        row1.append(InlineKeyboardButton("🗑 Clear", callback_data=f"q_clear_{token}"))
    row1.append(InlineKeyboardButton("🔄 Refresh", callback_data=f"q_refresh_{token}"))
    if row1:
        buttons.append(row1)

    base_url = Config.BASE_URL.rstrip('/') if hasattr(Config, 'BASE_URL') and Config.BASE_URL else "https://lyraz.ir"
    remote_url = f"{base_url}/remote/{token}"
    if is_admin:
        if remote_url.startswith('https://'):
            buttons.append([InlineKeyboardButton("🎛 Open Full Remote (WebApp)", web_app=WebAppInfo(url=remote_url))])
        elif remote_url.startswith('http://') and not any(h in remote_url for h in ['localhost', '127.0.0.1']):
            buttons.append([InlineKeyboardButton("🎛 Open Full Remote", url=remote_url)])
    else:
        live_url = f"{base_url}/live/{token}"
        if live_url.startswith('https://') or (live_url.startswith('http://') and not any(h in live_url for h in ['localhost', '127.0.0.1'])):
            buttons.append([InlineKeyboardButton("🎧 Open Live Player", url=live_url)])

    return InlineKeyboardMarkup(buttons)

def build_search_keyboard(results, query, page=0, page_size=4):
    """
    Paginated search results keyboard with Next/Previous navigation and status indicators.
    """
    import re
    total_results = len(results)
    total_pages = max(1, (total_results + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))

    start_idx = page * page_size
    end_idx = min(start_idx + page_size, total_results)
    page_items = results[start_idx:end_idx]

    buttons = []
    for i, song in enumerate(page_items, start=start_idx + 1):
        vid = song.get('videoId')
        s_title = song.get('title', 'Unknown Track')[:28]
        raw_artist = song.get('artists', [{'name': 'Unknown'}])[0]['name'] if song.get('artists') else "Unknown"
        s_artist = re.sub(r'\s*-\s*Topic$', '', raw_artist, flags=re.IGNORECASE).strip() or "Unknown"
        s_artist = s_artist[:18]

        btn_text = f"📥 {i}. {s_title} — {s_artist}"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"dl_{vid}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"sp_{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page + 1} / {total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"sp_{page + 1}"))

    buttons.append(nav_row)
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel_search")])

    msg_text = (
        f"🎶 *Search Results for:* _{query}_\n"
        f"Showing tracks *{start_idx + 1}-{end_idx}* of *{total_results}*:\n"
        f"Select a track below to play on your Hub:"
    )
    return msg_text, InlineKeyboardMarkup(buttons)
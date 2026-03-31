"""
PLUXO Combined Server
Runs both the API server and Telegram bot for Railway deployment
"""

import json
import os
import logging
import threading
import asyncio
import re
import random
import string
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, request, jsonify
from flask_cors import CORS
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8282351962:AAEaHEHx_6vG1XtogYXFKfIuYn1VKtT6_Fw")
OWNER_ID = int(os.getenv("OWNER_ID", "7173346586"))
OWNER_USERNAME = "@Xeeznk"
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "pluxo_secret_2024")
PORT = int(os.getenv("PORT", 5000))

# Data files
DATA_DIR = "bot_data"
ADMINS_FILE = os.path.join(DATA_DIR, "admins.json")
BALANCES_FILE = os.path.join(DATA_DIR, "balances.json")
PURCHASES_FILE = os.path.join(DATA_DIR, "purchases.json")
LOGS_FILE = os.path.join(DATA_DIR, "action_logs.json")
SHOP_PRODUCTS_FILE = "shop_products.json"
GAMES_FILE = os.path.join(DATA_DIR, "games.json")
PENDING_DEEPSEEK_FILE = os.path.join(DATA_DIR, "pending_deepseek.json")
PENDING_DEEPSEEK_LOCK = threading.Lock()
# HTML/DeepSeek tool: same as WEBHOOK_SECRET unless LEADBOT_API_SECRET is set
LEADBOT_TOOL_SECRET = os.getenv("LEADBOT_API_SECRET", WEBHOOK_SECRET)

SYSTEM_LOCKED = False
GAMES_LOCK = threading.Lock()

# ==================== LOGGING ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== DATA FUNCTIONS ====================
def ensure_data_dir():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)

def load_json(filepath, default=None):
    if default is None:
        default = {}
    try:
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Error loading {filepath}: {e}")
    return default

def save_json(filepath, data):
    ensure_data_dir()
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error(f"Error saving {filepath}: {e}")
        return False

def load_admins():
    data = load_json(ADMINS_FILE, {"admins": [OWNER_ID]})
    admins = set(data.get("admins", [OWNER_ID]))
    admins.add(OWNER_ID)
    return admins

def save_admins(admin_set):
    admin_list = list(admin_set)
    if OWNER_ID not in admin_list:
        admin_list.insert(0, OWNER_ID)
    save_json(ADMINS_FILE, {"admins": admin_list})

def load_balances():
    return load_json(BALANCES_FILE, {})

def save_balances(balances):
    save_json(BALANCES_FILE, balances)

def load_purchases():
    return load_json(PURCHASES_FILE, {"purchases": []})

def save_purchases(purchases):
    save_json(PURCHASES_FILE, purchases)

def log_action(admin_id, admin_name, action, details=""):
    logs = load_json(LOGS_FILE, {"logs": []})
    logs["logs"].append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "admin_id": admin_id,
        "admin_name": admin_name,
        "action": action,
        "details": details
    })
    logs["logs"] = logs["logs"][-1000:]
    save_json(LOGS_FILE, logs)

def generate_key(length=16):
    """Generate a random alphanumeric key"""
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

def parse_bulk_cards(text: str):
    """Parse bulk pipe-delimited cards (card|mm|yyyy|cvv)"""
    cards = []
    lines = text.strip().split('\n')
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Match: 5355851164846467|02|2026|358
        match = re.match(r'^(\d{15,16})\|(\d{1,2})\|(\d{4})\|(\d{3,4})$', line)
        if match:
            card_number = match.group(1)
            exp_month = match.group(2).zfill(2)
            exp_year = match.group(3)
            cvv = match.group(4)
            
            if len(card_number) == 15:
                card_number = '0' + card_number
            
            cards.append({
                'card_number': card_number,
                'exp_month': exp_month,
                'exp_year': exp_year,
                'cvv': cvv,
                'full_text': f"{card_number}|{exp_month}|{exp_year}|{cvv}",
                'name': '',
                'address': '',
                'city_state_zip': '',
                'country': ''
            })
    
    return cards

def parse_multiline_cards(text: str):
    """Parse multi-line cards with address info (card exp cvv + 4 lines of info)"""
    cards = []
    lines = text.strip().split('\n')
    
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        
        # Match: 4145670692391812 01/29 651
        match = re.match(r'^(\d{15,16})\s+(\d{2})/(\d{2})\s+(\d{3,4})$', line)
        if match:
            card_number = match.group(1)
            exp_month = match.group(2)
            exp_year_short = match.group(3)
            cvv = match.group(4)
            
            # Convert 2-digit year to 4-digit
            exp_year = '20' + exp_year_short
            
            if len(card_number) == 15:
                card_number = '0' + card_number
            
            # Get next 4 lines for address info
            name = lines[i + 1].strip() if i + 1 < len(lines) else ''
            address = lines[i + 2].strip() if i + 2 < len(lines) else ''
            city_state_zip = lines[i + 3].strip() if i + 3 < len(lines) else ''
            country = lines[i + 4].strip() if i + 4 < len(lines) else ''
            
            full_text = f"{card_number} {exp_month}/{exp_year_short} {cvv}\n{name}\n{address}\n{city_state_zip}\n{country}"
            
            cards.append({
                'card_number': card_number,
                'exp_month': exp_month,
                'exp_year': exp_year,
                'cvv': cvv,
                'full_text': full_text,
                'name': name,
                'address': address,
                'city_state_zip': city_state_zip,
                'country': country
            })
            
            i += 5  # Skip to next card block
            continue
        
        i += 1
    
    return cards

def parse_all_formats(text: str):
    """Try both card formats and return parsed cards"""
    # Try pipe format first
    cards = parse_bulk_cards(text)
    if cards:
        return cards
    
    # Try multi-line format
    cards = parse_multiline_cards(text)
    if cards:
        return cards
    
    return []

def get_brand_from_bin(bin_str):
    """Determine card brand from BIN"""
    if not bin_str or len(bin_str) < 6:
        return "VISA"
    first_digit = bin_str[0]
    if first_digit == "4":
        return "VISA"
    elif first_digit == "5":
        return "MASTERCARD"
    elif first_digit == "3":
        return "AMEX"
    return "VISA"

def get_shop_products():
    """Load shop products as a normalized list."""
    shop_products = load_json(SHOP_PRODUCTS_FILE, [])
    return shop_products if isinstance(shop_products, list) else []

def save_shop_products(shop_products):
    """Persist shop products to shared storage."""
    save_json(SHOP_PRODUCTS_FILE, shop_products)

def clear_shop_products():
    """Clear all shop stock and return removed count."""
    existing_products = get_shop_products()
    removed_count = len(existing_products)
    save_shop_products([])
    return removed_count

def remove_shop_products_by_ids(product_ids):
    """Remove products by id, return removed products and missing ids."""
    if not isinstance(product_ids, list):
        return [], []
    normalized_ids = []
    for pid in product_ids:
        pid_str = str(pid).strip()
        if pid_str:
            normalized_ids.append(pid_str)
    if not normalized_ids:
        return [], []

    existing_products = get_shop_products()
    id_set = set(normalized_ids)

    removed_products = []
    remaining_products = []
    for product in existing_products:
        product_id_str = str(product.get("id", "")).strip()
        if product_id_str in id_set:
            removed_products.append(product)
        else:
            remaining_products.append(product)

    removed_ids = {str(p.get("id", "")).strip() for p in removed_products}
    missing_ids = [pid for pid in normalized_ids if pid not in removed_ids]

    if removed_products:
        save_shop_products(remaining_products)

    return removed_products, missing_ids

def remove_shop_products_by_slots(slot_numbers):
    """Remove products by 1-based slot positions in current stock list."""
    if not isinstance(slot_numbers, list):
        return [], []

    normalized_slots = []
    for slot in slot_numbers:
        try:
            slot_int = int(slot)
            if slot_int not in normalized_slots:
                normalized_slots.append(slot_int)
        except (TypeError, ValueError):
            continue

    if not normalized_slots:
        return [], []

    shop_products = get_shop_products()
    total_products = len(shop_products)
    if total_products == 0:
        return [], normalized_slots

    valid_slots = [slot for slot in normalized_slots if 1 <= slot <= total_products]
    invalid_slots = [slot for slot in normalized_slots if slot < 1 or slot > total_products]
    if not valid_slots:
        return [], invalid_slots

    slot_set = set(valid_slots)
    removed_entries = []
    remaining_products = []

    for idx, product in enumerate(shop_products, start=1):
        if idx in slot_set:
            removed_entries.append({
                "slot": idx,
                "product": product
            })
        else:
            remaining_products.append(product)

    if removed_entries:
        save_shop_products(remaining_products)

    return removed_entries, invalid_slots

def default_games_state():
    return {
        "dice_bets": [],
        "dice_history": [],
        "blackjack_matches": [],
        "blackjack_history": []
    }

def load_games_state():
    state = load_json(GAMES_FILE, default_games_state())
    if not isinstance(state, dict):
        state = default_games_state()
    defaults = default_games_state()
    for key, fallback in defaults.items():
        if not isinstance(state.get(key), list):
            state[key] = fallback
    return state

def save_games_state(state):
    save_json(GAMES_FILE, state)

def valid_secret():
    return request.headers.get('X-Webhook-Secret', '') == WEBHOOK_SECRET

def valid_tool_secret():
    """Accept Pluxo webhook secret or X-Leadbot-Secret for HTML bin tool."""
    lead = (request.headers.get('X-Leadbot-Secret') or '').strip()
    wh = (request.headers.get('X-Webhook-Secret') or '').strip()
    return (lead and lead == LEADBOT_TOOL_SECRET) or (wh and wh == WEBHOOK_SECRET)

def redact_sync_line(line: str) -> str:
    """Keep only first 6 digits of PAN in field 1 (BIN); rest of pipe line unchanged."""
    line = (line or "").strip()
    if not line or "|" not in line:
        return line
    parts = line.split("|")
    card_raw = (parts[0] or "").strip().strip('"')
    digits = re.sub(r"\D", "", card_raw)
    if len(digits) >= 6:
        bin6 = digits[:6]
    elif digits:
        bin6 = digits.ljust(6, "0")[:6]
    else:
        bin6 = "000000"
    parts[0] = bin6
    return "|".join(parts)

def load_pending_deepseek():
    raw = load_json(PENDING_DEEPSEEK_FILE, None)
    if not isinstance(raw, dict):
        return {"groups": {}}
    groups = raw.get("groups")
    if not isinstance(groups, dict):
        groups = {}
    normalized = {}
    for bin_key, entries in groups.items():
        b = str(bin_key).strip()[:6]
        if not b:
            continue
        if not isinstance(entries, list):
            continue
        out = []
        for ent in entries:
            if isinstance(ent, dict):
                ln = ent.get("line") or ent.get("l") or ""
                pr = as_money(ent.get("price", 0))
            elif isinstance(ent, str):
                ln = ent
                pr = 0.0
            else:
                continue
            ln = redact_sync_line(ln)
            if ln:
                out.append({"line": ln, "price": pr})
        if out:
            normalized[b] = normalized.get(b, []) + out
    return {"groups": normalized}

def save_pending_deepseek(data):
    groups = data.get("groups") if isinstance(data, dict) else {}
    if not isinstance(groups, dict):
        groups = {}
    save_json(PENDING_DEEPSEEK_FILE, {"groups": groups})

def merge_pending_sync(incoming_groups: dict, price: float):
    """Merge redacted groups into pending file (thread-safe)."""
    price = as_money(price, 0)
    if not isinstance(incoming_groups, dict):
        return 0, 0
    lines_added = 0
    bins_touched = 0
    with PENDING_DEEPSEEK_LOCK:
        data = load_pending_deepseek()
        groups = data.setdefault("groups", {})
        for bin_key, lines in incoming_groups.items():
            if not isinstance(lines, list):
                continue
            b = str(bin_key).strip()[:6]
            if len(b) < 4:
                digits = re.sub(r"\D", "", str(bin_key))
                b = digits[:6].ljust(6, "0")[:6] if digits else ""
            if not b:
                continue
            bucket = groups.setdefault(b, [])
            before = len(bucket)
            for line in lines:
                if not isinstance(line, str):
                    continue
                red = redact_sync_line(line)
                if not red:
                    continue
                bucket.append({"line": red, "price": price})
                lines_added += 1
            if len(bucket) > before:
                bins_touched += 1
        save_pending_deepseek(data)
    return bins_touched, lines_added

def pending_deepseek_stats():
    with PENDING_DEEPSEEK_LOCK:
        data = load_pending_deepseek()
    groups = data.get("groups") or {}
    total_lines = sum(len(v) for v in groups.values() if isinstance(v, list))
    return len(groups), total_lines, groups

def apply_pending_deepseek_to_shop():
    """Publish all pending entries as shop rows (BIN-only, one listing per line)."""
    with PENDING_DEEPSEEK_LOCK:
        data = load_pending_deepseek()
        groups = data.get("groups") or {}
        if not groups:
            return 0
        snapshot = json.loads(json.dumps(groups))
    shop_products = get_shop_products()
    max_id = max([int(p.get("id", 0) or 0) for p in shop_products], default=0)
    existing_keys = {str(p.get("key", "")) for p in shop_products}
    added = 0
    for bin_str in sorted(snapshot.keys()):
        entries = snapshot.get(bin_str) or []
        if not isinstance(entries, list):
            continue
        for ent in entries:
            if isinstance(ent, dict):
                line = ent.get("line", "") or ""
                rec_price = as_money(ent.get("price", 0), 0)
            else:
                line = str(ent)
                rec_price = 0.0
            line = redact_sync_line(line)
            if not line:
                continue
            parts = line.split("|")
            digits = re.sub(r"\D", "", (parts[0] if parts else "") or "")
            bin6 = digits[:6].ljust(6, "0")[:6] if digits else bin_str[:6]
            brand = get_brand_from_bin(bin6)
            key = generate_key()
            while key in existing_keys:
                key = generate_key()
            existing_keys.add(key)
            max_id += 1
            product_entry = {
                "id": max_id,
                "bin": bin6,
                "brand": brand,
                "type": "CREDIT",
                "country": {
                    "flag": "🇺🇸",
                    "flagClass": "fi-us",
                    "code": "US",
                    "name": "USA"
                },
                "hasName": True,
                "hasAddress": True,
                "hasZip": True,
                "hasPhone": True,
                "hasMail": True,
                "hasSSN": True,
                "hasDOB": True,
                "bank": "BANK",
                "base": "2026_US_Base",
                "refundable": True,
                "price": str(rec_price) if rec_price > 0 else "0",
                "key": key,
                "seller_id": "html_bin_tool",
                "full_info": line,
            }
            shop_products.append(product_entry)
            added += 1
    if added > 0:
        save_shop_products(shop_products)
        with PENDING_DEEPSEEK_LOCK:
            save_pending_deepseek({"groups": {}})
    return added

async def notify_admins_sendout_pending(bin_count: int, line_count: int, preview_bins: str):
    """Tell admins to /confirm before website publish."""
    try:
        bot = Bot(token=BOT_TOKEN)
        message = (
            "📤 **Sendout (HTML bin tool)**\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"• BIN groups: **{bin_count}**\n"
            f"• Rows (listings): **{line_count}**\n"
            f"• BINs: `{preview_bins[:350]}{'…' if len(preview_bins) > 350 else ''}`\n\n"
            "Run **`/confirm`** to publish this batch to the **website shop**.\n"
            "Run **`/cancel_sendout`** to discard pending stock only."
        )
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(chat_id=admin_id, text=message, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Sendout notify admin {admin_id}: {e}")
    except Exception as e:
        logger.error(f"notify_admins_sendout_pending: {e}")

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def make_id(prefix):
    return f"{prefix}_{int(datetime.now(timezone.utc).timestamp() * 1000)}_{random.randint(1000, 9999)}"

def as_money(value, default=0.0):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return round(float(default), 2)

def ensure_balance_user(balances, username):
    username = str(username or "").lower().strip()
    if username not in balances or not isinstance(balances.get(username), dict):
        balances[username] = {"balance": 0.0, "totalRecharge": 0.0}
    balances[username]["balance"] = as_money(balances[username].get("balance", 0.0))
    balances[username]["totalRecharge"] = as_money(balances[username].get("totalRecharge", 0.0))
    return balances[username]

ADMIN_IDS = load_admins()

# ==================== FLASK APP ====================
app = Flask(__name__)
# Allow CORS from any origin (needed for GitHub Pages -> Railway)
CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=False)

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "ok", "server": "PLUXO API"})

@app.route('/api/stock-single', methods=['GET', 'OPTIONS'])
def api_stock_single():
    """Pending HTML-tool stock (BIN counts only, not live shop)."""
    if request.method == 'OPTIONS':
        return '', 204
    try:
        bin_count, line_count, groups = pending_deepseek_stats()
        bins_payload = []
        for bin_str in sorted(groups.keys()):
            entries = groups.get(bin_str) or []
            bins_payload.append({"bin": bin_str, "count": len(entries)})
        return jsonify({"bins": bins_payload, "pending_lines": line_count})
    except Exception as e:
        logger.error(f"stock-single error: {e}")
        return jsonify({"bins": [], "error": str(e)}), 200

@app.route('/api/sync-groups', methods=['POST', 'OPTIONS'])
def api_sync_groups():
    """Merge grouped lines from HTML tool; PAN reduced to BIN server-side."""
    if request.method == 'OPTIONS':
        return '', 204
    if not valid_tool_secret():
        return jsonify({"error": "Unauthorized", "ok": False}), 401
    try:
        data = request.json or {}
        groups = data.get("groups") or {}
        price = as_money(data.get("price", 0), 0)
        if not isinstance(groups, dict) or not groups:
            return jsonify({"ok": False, "error": "No groups"}), 400
        bins_touched, lines_added = merge_pending_sync(groups, price)
        return jsonify({"ok": True, "bins_touched": bins_touched, "lines_added": lines_added})
    except Exception as e:
        logger.error(f"sync-groups error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/api/sendout', methods=['POST', 'OPTIONS'])
def api_sendout():
    """Notify Telegram admins; website is updated only after /confirm."""
    if request.method == 'OPTIONS':
        return '', 204
    if not valid_tool_secret():
        return jsonify({"error": "Unauthorized", "ok": False}), 401
    try:
        bin_count, line_count, groups = pending_deepseek_stats()
        if line_count == 0:
            return jsonify({"ok": False, "error": "No pending stock. Sync from HTML first."}), 400
        preview_bins = ", ".join(sorted(groups.keys()))
        threading.Thread(
            target=lambda: asyncio.run(notify_admins_sendout_pending(bin_count, line_count, preview_bins)),
            daemon=True,
        ).start()
        return jsonify({"ok": True, "pending_bins": bin_count, "pending_lines": line_count})
    except Exception as e:
        logger.error(f"sendout error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/api/products', methods=['GET', 'OPTIONS'])
def get_products():
    """Serve shop products for the website"""
    if request.method == 'OPTIONS':
        return '', 204
    try:
        shop_products = get_shop_products()
        return jsonify(shop_products)
    except Exception as e:
        logger.error(f"Products API error: {e}")
        return jsonify([]), 200  # Return empty array on error

@app.route('/api/register', methods=['POST', 'OPTIONS'])
def webhook_register():
    if request.method == 'OPTIONS':
        return '', 204
    try:
        secret = request.headers.get('X-Webhook-Secret', '')
        if secret != WEBHOOK_SECRET:
            return jsonify({"error": "Unauthorized"}), 401
        
        data = request.json
        username = data.get('username', '').lower().strip()
        email = data.get('email', '')
        
        if not username:
            return jsonify({"error": "Username required"}), 400
        
        balances = load_balances()
        is_new = username not in balances
        
        if is_new:
            balances[username] = {
                "balance": 0,
                "totalRecharge": 0,
                "email": email,
                "registeredAt": datetime.now(timezone.utc).isoformat()
            }
            save_balances(balances)
            log_action(0, "WEBSITE", "NEW_USER", f"User registered: {username}")
            logger.info(f"New user registered: {username}")
        
        return jsonify({"success": True, "username": username, "isNew": is_new})
    except Exception as e:
        logger.error(f"Register error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/balance/<username>', methods=['GET', 'OPTIONS'])
def get_user_balance(username):
    if request.method == 'OPTIONS':
        return '', 204
    try:
        secret = request.headers.get('X-Webhook-Secret', '')
        if secret != WEBHOOK_SECRET:
            return jsonify({"error": "Unauthorized"}), 401
        
        username = username.lower().strip()
        balances = load_balances()
        
        if username in balances:
            user_data = balances[username]
            return jsonify({
                "success": True,
                "username": username,
                "balance": user_data.get("balance", 0),
                "totalRecharge": user_data.get("totalRecharge", 0)
            })
        else:
            return jsonify({"success": True, "username": username, "balance": 0, "totalRecharge": 0})
    except Exception as e:
        logger.error(f"Balance API error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/balance/update', methods=['POST', 'OPTIONS'])
def update_user_balance():
    if request.method == 'OPTIONS':
        return '', 204
    try:
        secret = request.headers.get('X-Webhook-Secret', '')
        if secret != WEBHOOK_SECRET:
            return jsonify({"error": "Unauthorized"}), 401
        
        data = request.json
        username = data.get('username', '').lower().strip()
        action = data.get('action', '')
        amount = float(data.get('amount', 0))
        
        if not username or not action or amount <= 0:
            return jsonify({"error": "Invalid parameters"}), 400
        
        balances = load_balances()
        if username not in balances:
            balances[username] = {"balance": 0, "totalRecharge": 0}
        
        old_balance = balances[username].get("balance", 0)
        
        if action == 'subtract':
            if old_balance < amount:
                return jsonify({"error": "Insufficient balance"}), 400
            new_balance = old_balance - amount
        elif action == 'add':
            new_balance = old_balance + amount
        else:
            return jsonify({"error": "Invalid action"}), 400
        
        balances[username]["balance"] = new_balance
        save_balances(balances)
        
        return jsonify({"success": True, "username": username, "oldBalance": old_balance, "newBalance": new_balance})
    except Exception as e:
        logger.error(f"Balance update error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/purchase/notify', methods=['POST', 'OPTIONS'])
def notify_purchase():
    """Notify admins about a purchase made on the website"""
    if request.method == 'OPTIONS':
        return '', 204
    try:
        secret = request.headers.get('X-Webhook-Secret', '')
        if secret != WEBHOOK_SECRET:
            return jsonify({"error": "Unauthorized"}), 401
        
        data = request.json
        username = data.get('username', '').lower().strip()
        item_count = data.get('item_count', 1)
        total_amount = float(data.get('total_amount', 0))
        product_ids = data.get('product_ids', [])
        stock_slots = data.get('stock_slots', [])
        
        if not username:
            return jsonify({"error": "Username required"}), 400

        removed_count = 0
        missing_ids = []
        invalid_slots = []

        # Auto-remove purchased stock (single or multiple) when checkout notifies backend.
        if isinstance(product_ids, list) and product_ids:
            removed_products, missing_ids = remove_shop_products_by_ids(product_ids)
            removed_count = len(removed_products)
        elif isinstance(stock_slots, list) and stock_slots:
            removed_entries, invalid_slots = remove_shop_products_by_slots(stock_slots)
            removed_count = len(removed_entries)
        
        # Send notification to all admins via Telegram bot (run in background)
        threading.Thread(target=lambda: asyncio.run(notify_admins_purchase(username, item_count, total_amount)), daemon=True).start()
        
        return jsonify({
            "success": True,
            "message": "Notification sent",
            "removed_count": removed_count,
            "missing_ids": missing_ids,
            "invalid_slots": invalid_slots
        })
    except Exception as e:
        logger.error(f"Purchase notification error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/purchase/checkout', methods=['POST', 'OPTIONS'])
def purchase_checkout():
    """Atomically charge balance and remove purchased products from stock."""
    if request.method == 'OPTIONS':
        return '', 204
    try:
        secret = request.headers.get('X-Webhook-Secret', '')
        if secret != WEBHOOK_SECRET:
            return jsonify({"error": "Unauthorized"}), 401

        data = request.json or {}
        username = str(data.get('username', '')).lower().strip()
        items = data.get('items', [])

        if not username or not isinstance(items, list) or len(items) == 0:
            return jsonify({"error": "Invalid parameters"}), 400

        product_ids = []
        total_amount = 0.0
        for item in items:
            if not isinstance(item, dict):
                continue
            product_id = item.get("productId")
            price = float(item.get("price", 0))
            if product_id is None or price <= 0:
                continue
            product_ids.append(product_id)
            total_amount += price

        if not product_ids or total_amount <= 0:
            return jsonify({"error": "Invalid checkout items"}), 400

        # 1) Validate user balance
        balances = load_balances()
        if username not in balances:
            balances[username] = {"balance": 0, "totalRecharge": 0}
        old_balance = float(balances[username].get("balance", 0))
        if old_balance < total_amount:
            return jsonify({"error": "Insufficient balance"}), 400

        # 2) Remove products from shared stock first (so they are not sellable anymore)
        removed_products, missing_ids = remove_shop_products_by_ids(product_ids)
        if missing_ids:
            return jsonify({"error": "Some items are no longer available", "missing_ids": missing_ids}), 409
        if len(removed_products) != len(product_ids):
            return jsonify({"error": "Checkout failed: product mismatch"}), 409

        # 3) Charge balance after stock lock-in
        new_balance = old_balance - total_amount
        balances[username]["balance"] = new_balance
        save_balances(balances)

        # 4) Return purchased product payload with keys/full info
        purchased_items = []
        removed_by_id = {str(p.get("id", "")).strip(): p for p in removed_products}
        for item in items:
            pid_str = str(item.get("productId", "")).strip()
            product = removed_by_id.get(pid_str)
            if not product:
                continue
            purchased_items.append({
                "productId": product.get("id"),
                "bin": product.get("bin", ""),
                "brand": product.get("brand", ""),
                "bank": product.get("bank", "BANK"),
                "base": product.get("base", "2026_US_Base"),
                "price": float(product.get("price", item.get("price", 0))),
                "refundable": bool(product.get("refundable", True)),
                "key": product.get("key", ""),
                "full_info": product.get("full_info", "")
            })

        log_action(
            0,
            "WEBSITE",
            "CHECKOUT",
            f"User {username} purchased {len(purchased_items)} item(s), total ${total_amount:.2f}"
        )

        return jsonify({
            "success": True,
            "username": username,
            "oldBalance": old_balance,
            "newBalance": new_balance,
            "totalAmount": total_amount,
            "itemCount": len(purchased_items),
            "items": purchased_items
        })
    except Exception as e:
        logger.error(f"Purchase checkout error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/games/dice/bets', methods=['GET', 'OPTIONS'])
def api_get_dice_bets():
    if request.method == 'OPTIONS':
        return '', 204
    if not valid_secret():
        return jsonify({"error": "Unauthorized"}), 401
    with GAMES_LOCK:
        state = load_games_state()
        return jsonify({"success": True, "bets": state["dice_bets"]})

@app.route('/api/games/dice/history', methods=['GET', 'OPTIONS'])
def api_get_dice_history():
    if request.method == 'OPTIONS':
        return '', 204
    if not valid_secret():
        return jsonify({"error": "Unauthorized"}), 401
    with GAMES_LOCK:
        state = load_games_state()
        return jsonify({"success": True, "history": state["dice_history"]})

@app.route('/api/games/dice/create', methods=['POST', 'OPTIONS'])
def api_create_dice_bet():
    if request.method == 'OPTIONS':
        return '', 204
    if not valid_secret():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        data = request.json or {}
        creator = str(data.get("creator", "")).strip().lower()
        creator_name = str(data.get("creatorName", "")).strip()
        amount = as_money(data.get("amount", 0))
        if not creator or not creator_name or amount < 1 or amount > 25:
            return jsonify({"error": "Invalid parameters"}), 400

        with GAMES_LOCK:
            state = load_games_state()
            balances = load_balances()
            creator_data = ensure_balance_user(balances, creator)
            for bet in state["dice_bets"]:
                if bet.get("status") == "waiting" and (bet.get("creator") == creator or bet.get("opponent") == creator):
                    return jsonify({"error": "You already have an active waiting bet"}), 400

            if creator_data["balance"] < amount:
                return jsonify({"error": "Insufficient balance"}), 400

            creator_data["balance"] = as_money(creator_data["balance"] - amount)

            new_bet = {
                "id": make_id("DICE"),
                "creator": creator,
                "creatorName": creator_name,
                "opponent": None,
                "opponentName": None,
                "amount": f"{amount:.2f}",
                "status": "waiting",
                "creatorDebited": True,
                "creatorRoll": None,
                "opponentRoll": None,
                "winner": None,
                "winnerName": None,
                "createdAt": now_iso(),
                "completedAt": None
            }
            state["dice_bets"].append(new_bet)
            save_balances(balances)
            save_games_state(state)
            return jsonify({"success": True, "bet": new_bet, "newBalance": creator_data["balance"]})
    except Exception as e:
        logger.error(f"Create dice bet error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/games/dice/cancel', methods=['POST', 'OPTIONS'])
def api_cancel_dice_bet():
    if request.method == 'OPTIONS':
        return '', 204
    if not valid_secret():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        data = request.json or {}
        bet_id = str(data.get("betId", "")).strip()
        username = str(data.get("username", "")).strip().lower()
        if not bet_id or not username:
            return jsonify({"error": "Invalid parameters"}), 400

        with GAMES_LOCK:
            state = load_games_state()
            balances = load_balances()
            idx = next((i for i, b in enumerate(state["dice_bets"]) if b.get("id") == bet_id), -1)
            if idx < 0:
                return jsonify({"error": "Bet not found"}), 404
            bet = state["dice_bets"][idx]
            if bet.get("creator") != username:
                return jsonify({"error": "Only creator can cancel"}), 403
            if bet.get("status") != "waiting":
                return jsonify({"error": "Bet cannot be cancelled"}), 400
            state["dice_bets"].pop(idx)
            amount = as_money(bet.get("amount", 0))
            creator_data = ensure_balance_user(balances, username)
            refunded = 0.0
            if bet.get("creatorDebited", False):
                creator_data["balance"] = as_money(creator_data["balance"] + amount)
                refunded = amount
            save_balances(balances)
            save_games_state(state)
            return jsonify({"success": True, "amount": refunded, "newBalance": creator_data["balance"]})
    except Exception as e:
        logger.error(f"Cancel dice bet error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/games/dice/accept', methods=['POST', 'OPTIONS'])
def api_accept_dice_bet():
    if request.method == 'OPTIONS':
        return '', 204
    if not valid_secret():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        data = request.json or {}
        bet_id = str(data.get("betId", "")).strip()
        opponent = str(data.get("opponent", "")).strip().lower()
        opponent_name = str(data.get("opponentName", "")).strip()
        if not bet_id or not opponent or not opponent_name:
            return jsonify({"error": "Invalid parameters"}), 400

        with GAMES_LOCK:
            state = load_games_state()
            balances = load_balances()
            idx = next((i for i, b in enumerate(state["dice_bets"]) if b.get("id") == bet_id), -1)
            if idx < 0:
                return jsonify({"error": "Bet not found"}), 404
            bet = state["dice_bets"][idx]
            if bet.get("status") != "waiting":
                return jsonify({"error": "Bet is no longer available"}), 400
            if bet.get("creator") == opponent:
                return jsonify({"error": "Cannot accept your own bet"}), 400

            amount = as_money(bet.get("amount", 0))
            creator_username = str(bet.get("creator", "")).lower().strip()
            creator_data = ensure_balance_user(balances, creator_username)
            opponent_data = ensure_balance_user(balances, opponent)

            # Backward-compatible safety: older matches may not have creator stake debited.
            if not bet.get("creatorDebited", False):
                if creator_data["balance"] < amount:
                    state["dice_bets"].pop(idx)
                    save_games_state(state)
                    return jsonify({"error": "Creator has insufficient balance. Match cancelled."}), 409
                creator_data["balance"] = as_money(creator_data["balance"] - amount)
                bet["creatorDebited"] = True

            if opponent_data["balance"] < amount:
                return jsonify({"error": "Insufficient balance"}), 400
            opponent_data["balance"] = as_money(opponent_data["balance"] - amount)

            creator_roll = random.randint(1, 6)
            opponent_roll = random.randint(1, 6)
            winner = "tie"
            winner_name = "Tie"
            if creator_roll > opponent_roll:
                winner = bet.get("creator")
                winner_name = bet.get("creatorName")
            elif opponent_roll > creator_roll:
                winner = opponent
                winner_name = opponent_name

            creator_payout = 0.0
            opponent_payout = 0.0
            if winner == creator_username:
                creator_payout = as_money(amount * 2)
                creator_data["balance"] = as_money(creator_data["balance"] + creator_payout)
            elif winner == opponent:
                opponent_payout = as_money(amount * 2)
                opponent_data["balance"] = as_money(opponent_data["balance"] + opponent_payout)
            else:
                # Tie: return 50% to each side (house keeps 50% total)
                creator_payout = as_money(amount * 0.5)
                opponent_payout = as_money(amount * 0.5)
                creator_data["balance"] = as_money(creator_data["balance"] + creator_payout)
                opponent_data["balance"] = as_money(opponent_data["balance"] + opponent_payout)

            completed = {
                **bet,
                "opponent": opponent,
                "opponentName": opponent_name,
                "status": "completed",
                "creatorRoll": creator_roll,
                "opponentRoll": opponent_roll,
                "winner": winner,
                "winnerName": winner_name,
                "creatorPayout": creator_payout,
                "opponentPayout": opponent_payout,
                "creatorBalanceAfter": creator_data["balance"],
                "opponentBalanceAfter": opponent_data["balance"],
                "completedAt": now_iso()
            }

            state["dice_bets"].pop(idx)
            state["dice_history"].insert(0, completed)
            state["dice_history"] = state["dice_history"][:100]
            save_balances(balances)
            save_games_state(state)
            return jsonify({
                "success": True,
                "result": completed,
                "balances": {
                    "creator": creator_data["balance"],
                    "opponent": opponent_data["balance"]
                },
                "viewerBalance": opponent_data["balance"]
            })
    except Exception as e:
        logger.error(f"Accept dice bet error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/games/blackjack/matches', methods=['GET', 'OPTIONS'])
def api_get_blackjack_matches():
    if request.method == 'OPTIONS':
        return '', 204
    if not valid_secret():
        return jsonify({"error": "Unauthorized"}), 401
    with GAMES_LOCK:
        state = load_games_state()
        return jsonify({"success": True, "matches": state["blackjack_matches"]})

@app.route('/api/games/blackjack/history', methods=['GET', 'OPTIONS'])
def api_get_blackjack_history():
    if request.method == 'OPTIONS':
        return '', 204
    if not valid_secret():
        return jsonify({"error": "Unauthorized"}), 401
    with GAMES_LOCK:
        state = load_games_state()
        return jsonify({"success": True, "history": state["blackjack_history"]})

@app.route('/api/games/blackjack/create', methods=['POST', 'OPTIONS'])
def api_create_blackjack_match():
    if request.method == 'OPTIONS':
        return '', 204
    if not valid_secret():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        data = request.json or {}
        creator = str(data.get("creator", "")).strip().lower()
        creator_name = str(data.get("creatorName", "")).strip()
        amount = as_money(data.get("amount", 0))
        if not creator or not creator_name or amount < 1 or amount > 25:
            return jsonify({"error": "Invalid parameters"}), 400

        with GAMES_LOCK:
            state = load_games_state()
            balances = load_balances()
            creator_data = ensure_balance_user(balances, creator)
            existing = next((m for m in state["blackjack_matches"] if m.get("creator") == creator and m.get("status") == "waiting"), None)
            if existing:
                return jsonify({"error": "You already have an open match"}), 400

            if creator_data["balance"] < amount:
                return jsonify({"error": "Insufficient balance"}), 400
            creator_data["balance"] = as_money(creator_data["balance"] - amount)

            match = {
                "id": make_id("BJ"),
                "creator": creator,
                "creatorName": creator_name,
                "opponent": None,
                "opponentName": None,
                "amount": f"{amount:.2f}",
                "status": "waiting",
                "creatorDebited": True,
                "createdAt": now_iso()
            }
            state["blackjack_matches"].append(match)
            save_balances(balances)
            save_games_state(state)
            return jsonify({"success": True, "match": match, "newBalance": creator_data["balance"]})
    except Exception as e:
        logger.error(f"Create blackjack match error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/games/blackjack/cancel', methods=['POST', 'OPTIONS'])
def api_cancel_blackjack_match():
    if request.method == 'OPTIONS':
        return '', 204
    if not valid_secret():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        data = request.json or {}
        match_id = str(data.get("matchId", "")).strip()
        username = str(data.get("username", "")).strip().lower()
        if not match_id or not username:
            return jsonify({"error": "Invalid parameters"}), 400

        with GAMES_LOCK:
            state = load_games_state()
            balances = load_balances()
            idx = next((i for i, m in enumerate(state["blackjack_matches"]) if m.get("id") == match_id), -1)
            if idx < 0:
                return jsonify({"error": "Match not found"}), 404
            match = state["blackjack_matches"][idx]
            if match.get("creator") != username:
                return jsonify({"error": "Only creator can cancel"}), 403
            if match.get("status") != "waiting":
                return jsonify({"error": "Match cannot be cancelled"}), 400

            state["blackjack_matches"].pop(idx)
            amount = as_money(match.get("amount", 0))
            creator_data = ensure_balance_user(balances, username)
            refunded = 0.0
            if match.get("creatorDebited", False):
                creator_data["balance"] = as_money(creator_data["balance"] + amount)
                refunded = amount
            save_balances(balances)
            save_games_state(state)
            return jsonify({"success": True, "amount": refunded, "newBalance": creator_data["balance"]})
    except Exception as e:
        logger.error(f"Cancel blackjack match error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/games/blackjack/join', methods=['POST', 'OPTIONS'])
def api_join_blackjack_match():
    if request.method == 'OPTIONS':
        return '', 204
    if not valid_secret():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        data = request.json or {}
        match_id = str(data.get("matchId", "")).strip()
        opponent = str(data.get("opponent", "")).strip().lower()
        opponent_name = str(data.get("opponentName", "")).strip()
        if not match_id or not opponent or not opponent_name:
            return jsonify({"error": "Invalid parameters"}), 400

        with GAMES_LOCK:
            state = load_games_state()
            balances = load_balances()
            idx = next((i for i, m in enumerate(state["blackjack_matches"]) if m.get("id") == match_id), -1)
            if idx < 0:
                return jsonify({"error": "Match not found"}), 404
            match = state["blackjack_matches"][idx]
            if match.get("status") != "waiting":
                return jsonify({"error": "Match not available"}), 400
            if match.get("creator") == opponent:
                return jsonify({"error": "Cannot join your own match"}), 400

            amount = as_money(match.get("amount", 0))
            creator_username = str(match.get("creator", "")).lower().strip()
            creator_data = ensure_balance_user(balances, creator_username)
            opponent_data = ensure_balance_user(balances, opponent)

            # Backward-compatible safety for old waiting matches.
            if not match.get("creatorDebited", False):
                if creator_data["balance"] < amount:
                    state["blackjack_matches"].pop(idx)
                    save_games_state(state)
                    return jsonify({"error": "Creator has insufficient balance. Match cancelled."}), 409
                creator_data["balance"] = as_money(creator_data["balance"] - amount)
                match["creatorDebited"] = True

            if opponent_data["balance"] < amount:
                return jsonify({"error": "Insufficient balance"}), 400
            opponent_data["balance"] = as_money(opponent_data["balance"] - amount)

            creator_score = random.randint(12, 22)
            opponent_score = random.randint(12, 22)
            creator_bust = creator_score > 21
            opponent_bust = opponent_score > 21

            winner = "tie"
            winner_name = "Tie"
            if creator_bust and opponent_bust:
                winner = "tie"
                winner_name = "Tie"
            elif creator_bust:
                winner = opponent
                winner_name = opponent_name
            elif opponent_bust:
                winner = match.get("creator")
                winner_name = match.get("creatorName")
            elif creator_score > opponent_score:
                winner = match.get("creator")
                winner_name = match.get("creatorName")
            elif opponent_score > creator_score:
                winner = opponent
                winner_name = opponent_name

            creator_payout = 0.0
            opponent_payout = 0.0
            if winner == creator_username:
                creator_payout = as_money(amount * 2)
                creator_data["balance"] = as_money(creator_data["balance"] + creator_payout)
            elif winner == opponent:
                opponent_payout = as_money(amount * 2)
                opponent_data["balance"] = as_money(opponent_data["balance"] + opponent_payout)
            else:
                # Tie: return 50% to each side (house keeps 50% total)
                creator_payout = as_money(amount * 0.5)
                opponent_payout = as_money(amount * 0.5)
                creator_data["balance"] = as_money(creator_data["balance"] + creator_payout)
                opponent_data["balance"] = as_money(opponent_data["balance"] + opponent_payout)

            completed = {
                **match,
                "opponent": opponent,
                "opponentName": opponent_name,
                "creatorScore": creator_score,
                "opponentScore": opponent_score,
                "winner": winner,
                "winnerName": winner_name,
                "creatorPayout": creator_payout,
                "opponentPayout": opponent_payout,
                "creatorBalanceAfter": creator_data["balance"],
                "opponentBalanceAfter": opponent_data["balance"],
                "status": "completed",
                "completedAt": now_iso()
            }

            state["blackjack_matches"].pop(idx)
            state["blackjack_history"].insert(0, completed)
            state["blackjack_history"] = state["blackjack_history"][:100]
            save_balances(balances)
            save_games_state(state)
            return jsonify({
                "success": True,
                "result": completed,
                "balances": {
                    "creator": creator_data["balance"],
                    "opponent": opponent_data["balance"]
                },
                "viewerBalance": opponent_data["balance"]
            })
    except Exception as e:
        logger.error(f"Join blackjack match error: {e}")
        return jsonify({"error": str(e)}), 500

async def notify_admins_purchase(username, item_count, total_amount):
    """Send purchase notification to all admins"""
    try:
        bot = Bot(token=BOT_TOKEN)
        purchase_time = datetime.now(timezone.utc)
        date_str = purchase_time.strftime('%Y-%m-%d')
        time_str = purchase_time.strftime('%H:%M:%S UTC')
        
        message = f"""🛒 **Purchase Made**
━━━━━━━━━━━━━━━━━━
👤 Username: `{username}`
📦 Items: {item_count}
💵 Total: **${total_amount:.2f}**
📅 Date: {date_str}
🕐 Time: {time_str}
━━━━━━━━━━━━━━━━━━"""
        
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(chat_id=admin_id, text=message, parse_mode='Markdown')
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")
    except Exception as e:
        logger.error(f"Error sending purchase notification: {e}")

# ==================== TELEGRAM BOT DECORATORS ====================
def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            return
        return await func(update, context)
    return wrapper

def owner_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != OWNER_ID:
            return
        return await func(update, context)
    return wrapper

# ==================== TELEGRAM BOT COMMANDS ====================
@admin_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_owner = user.id == OWNER_ID
    msg = f"""
🔐 **PLUXO Admin Bot**
━━━━━━━━━━━━━━━━━━
👤 Welcome, {user.first_name}
👑 Role: {"OWNER" if is_owner else "ADMIN"}

💰 **Balance:**
/balance <user> - View balance
/setbalance <user> <amt> - Set balance
/addbalance <user> <amt> - Add balance
/removebalance <user> <amt> - Remove balance
/users - List all users

📦 **Stock:**
/stock <price> <cards> - Add cards to shop
/removestockslot <n[,n..]> - Remove slot(s)
/clearstock - Clear all shop stock
/confirm - Publish **pending** HTML-tool batch to website
/cancel_sendout - Discard pending HTML-tool stock (no publish)

👥 **Admin (Owner only):**
/addadmin <id> - Add admin
/removeadmin <id> - Remove admin
/admins - List admins
"""
    await update.message.reply_text(msg, parse_mode='Markdown')
    stock_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧹 Clear Stock", callback_data="stock_clear_prompt")]
    ])
    await update.message.reply_text(
        "Quick stock action:",
        reply_markup=stock_keyboard
    )

@admin_only
async def view_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Usage: /balance <username>")
        return
    username = context.args[0].lower().strip().lstrip('@')
    balances = load_balances()
    if username not in balances:
        await update.message.reply_text(f"❌ User `{username}` not found.", parse_mode='Markdown')
        return
    user_data = balances[username]
    await update.message.reply_text(f"""
💰 **Balance Info**
━━━━━━━━━━━━━━━━━━
👤 User: `{username}`
💵 Balance: **${user_data.get('balance', 0):.2f}**
📊 Total Recharge: ${user_data.get('totalRecharge', 0):.2f}
""", parse_mode='Markdown')

@admin_only
async def set_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("❌ Usage: /setbalance <username> <amount>")
        return
    username = context.args[0].lower().strip().lstrip('@')
    try:
        amount = float(context.args[1].replace('$', '').replace(',', ''))
    except ValueError:
        await update.message.reply_text("❌ Invalid amount")
        return
    balances = load_balances()
    old_balance = balances.get(username, {}).get("balance", 0)
    if username not in balances:
        balances[username] = {"balance": 0, "totalRecharge": 0, "registeredAt": datetime.now(timezone.utc).isoformat()}
    balances[username]["balance"] = amount
    save_balances(balances)
    user = update.effective_user
    log_action(user.id, user.first_name, "SET_BALANCE", f"{username}: ${old_balance:.2f} -> ${amount:.2f}")
    await update.message.reply_text(f"✅ **Balance Set**\n👤 User: `{username}`\n💵 New Balance: **${amount:.2f}**", parse_mode='Markdown')

@admin_only
async def add_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("❌ Usage: /addbalance <username> <amount>")
        return
    username = context.args[0].lower().strip().lstrip('@')
    try:
        amount = float(context.args[1].replace('$', '').replace(',', ''))
    except ValueError:
        await update.message.reply_text("❌ Invalid amount")
        return
    balances = load_balances()
    if username not in balances:
        balances[username] = {"balance": 0, "totalRecharge": 0, "registeredAt": datetime.now(timezone.utc).isoformat()}
    old_balance = balances[username].get("balance", 0)
    new_balance = old_balance + amount
    balances[username]["balance"] = new_balance
    balances[username]["totalRecharge"] = balances[username].get("totalRecharge", 0) + amount
    save_balances(balances)
    user = update.effective_user
    log_action(user.id, user.first_name, "ADD_BALANCE", f"{username}: +${amount:.2f}")
    await update.message.reply_text(f"""
✅ **Balance Added**
👤 User: `{username}`
➕ Added: +${amount:.2f}
💵 New Balance: **${new_balance:.2f}**
""", parse_mode='Markdown')

@admin_only
async def remove_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("❌ Usage: /removebalance <username> <amount>")
        return
    username = context.args[0].lower().strip().lstrip('@')
    try:
        amount = float(context.args[1].replace('$', '').replace(',', ''))
    except ValueError:
        await update.message.reply_text("❌ Invalid amount")
        return
    balances = load_balances()
    if username not in balances:
        await update.message.reply_text(f"❌ User `{username}` not found.", parse_mode='Markdown')
        return
    old_balance = balances[username].get("balance", 0)
    new_balance = max(0, old_balance - amount)
    balances[username]["balance"] = new_balance
    save_balances(balances)
    user = update.effective_user
    log_action(user.id, user.first_name, "REMOVE_BALANCE", f"{username}: -${amount:.2f}")
    await update.message.reply_text(f"""
✅ **Balance Removed**
👤 User: `{username}`
➖ Removed: -${amount:.2f}
💵 New Balance: **${new_balance:.2f}**
""", parse_mode='Markdown')

@admin_only
async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    balances = load_balances()
    if not balances:
        await update.message.reply_text("📭 No users registered yet.")
        return
    msg = "👥 **Registered Users**\n━━━━━━━━━━━━━━━━━━\n"
    for i, (username, data) in enumerate(balances.items(), 1):
        balance = data.get("balance", 0)
        reg_date = data.get("registeredAt", "Unknown")[:10]
        msg += f"{i}. `{username}` - ${balance:.2f} ({reg_date})\n"
    msg += f"\n📊 Total Users: {len(balances)}"
    await update.message.reply_text(msg, parse_mode='Markdown')

@owner_only
async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ADMIN_IDS
    if not context.args:
        await update.message.reply_text("❌ Usage: /addadmin <user_id>")
        return
    try:
        new_admin_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID")
        return
    if new_admin_id in ADMIN_IDS:
        await update.message.reply_text("⚠️ User is already an admin")
        return
    ADMIN_IDS.add(new_admin_id)
    save_admins(ADMIN_IDS)
    log_action(update.effective_user.id, update.effective_user.first_name, "ADD_ADMIN", f"Added admin: {new_admin_id}")
    await update.message.reply_text(f"✅ Added admin: `{new_admin_id}`", parse_mode='Markdown')

@owner_only
async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ADMIN_IDS
    if not context.args:
        await update.message.reply_text("❌ Usage: /removeadmin <user_id>")
        return
    try:
        admin_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID")
        return
    if admin_id == OWNER_ID:
        await update.message.reply_text("❌ Cannot remove owner")
        return
    if admin_id not in ADMIN_IDS:
        await update.message.reply_text("⚠️ User is not an admin")
        return
    ADMIN_IDS.discard(admin_id)
    save_admins(ADMIN_IDS)
    log_action(update.effective_user.id, update.effective_user.first_name, "REMOVE_ADMIN", f"Removed admin: {admin_id}")
    await update.message.reply_text(f"✅ Removed admin: `{admin_id}`", parse_mode='Markdown')

@owner_only
async def list_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "👥 **Admin List**\n━━━━━━━━━━━━━━━━━━\n"
    for admin_id in ADMIN_IDS:
        role = "👑 OWNER" if admin_id == OWNER_ID else "🔐 Admin"
        msg += f"• `{admin_id}` - {role}\n"
    await update.message.reply_text(msg, parse_mode='Markdown')

@admin_only
async def add_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add stock with price and BIN, optional key: /stock [price] [BIN] [key]"""
    if len(context.args) < 2:
        await update.message.reply_text("""📦 **Stock Command Usage:**

```
/stock [price] [BIN] [key]
```

**Examples:**
```
/stock 15 5355
/stock 15 5355 IEVJ073E3TFBZVJC
/stock $15 5355 IEVJ073E3TFBZVJC
/stock 12.50 4145
```

**Options:**
- Price: Number (with or without $)
- BIN: 4-6 digits
- Key: Optional, 16 characters (auto-generated if not provided)

This will create a product with:
- Price: $15.00
- BIN: 5355
- Display: `5355********** $15.0`
- Key: Provided or auto-generated""", parse_mode='Markdown')
        return
    
    # Parse price
    try:
        price_str = context.args[0].replace('$', '').replace(',', '')
        price = float(price_str)
        if price <= 0 or price > 10000:
            await update.message.reply_text("❌ Invalid price! Price must be between $0.01 and $10,000", parse_mode='Markdown')
            return
    except ValueError:
        await update.message.reply_text("❌ Invalid price format! Use a number like: 15 or 12.50", parse_mode='Markdown')
        return
    
    # Parse BIN (first 4-6 digits)
    bin_input = context.args[1].strip()
    if not bin_input.isdigit():
        await update.message.reply_text("❌ Invalid BIN! BIN must be digits only (4-6 digits)", parse_mode='Markdown')
        return
    
    # Ensure BIN is 4-6 digits
    if len(bin_input) < 4 or len(bin_input) > 6:
        await update.message.reply_text("❌ BIN must be 4-6 digits!", parse_mode='Markdown')
        return
    
    # Pad BIN to 6 digits for consistency (first 6 digits)
    bin_str = bin_input[:6].ljust(6, '0') if len(bin_input) < 6 else bin_input[:6]
    
    # Parse optional key (handle "Key: XXX" format or just the key)
    provided_key = None
    if len(context.args) >= 3:
        # Join all remaining args in case key has spaces or "Key:" prefix
        remaining_args = ' '.join(context.args[2:]).strip()
        logger.info(f"Raw key input: {remaining_args}")
        
        # Handle "Key: IEVJ073E3TFBZVJC" format
        if remaining_args.lower().startswith('key:'):
            remaining_args = remaining_args[4:].strip()
        
        # Remove any non-alphanumeric characters and uppercase
        cleaned_key = ''.join(c for c in remaining_args if c.isalnum()).upper()
        logger.info(f"Cleaned key: {cleaned_key}")
        
        # Validate key format (alphanumeric, at least 8 chars)
        if cleaned_key and len(cleaned_key) >= 8:
            provided_key = cleaned_key
            logger.info(f"Using provided key: {provided_key}")
        else:
            await update.message.reply_text(f"❌ Invalid key format! Key must be alphanumeric and at least 8 characters. Got: `{cleaned_key}` (length: {len(cleaned_key)})", parse_mode='Markdown')
            return
    
    # Load existing shop products
    shop_products = get_shop_products()
    
    # Get next ID
    next_id = max([p.get('id', 0) for p in shop_products], default=0) + 1
    
    # Use provided key or generate unique key
    existing_keys = {p.get('key', '') for p in shop_products}
    if provided_key:
        # Check if key already exists
        if provided_key in existing_keys:
            await update.message.reply_text(f"❌ Key `{provided_key}` already exists! Please use a different key.", parse_mode='Markdown')
            return
        key = provided_key
    else:
        # Generate unique key
        key = generate_key()
        while key in existing_keys:
            key = generate_key()
    
    # Determine brand from BIN
    brand = get_brand_from_bin(bin_str)
    
    # Create product entry matching shop_products.json format
    product_entry = {
        "id": next_id,
        "bin": bin_str,
        "brand": brand,
        "type": "CREDIT",
        "country": {
            "flag": "🇺🇸",
            "flagClass": "fi-us",
            "code": "US",
            "name": "USA"
        },
        "hasName": True,
        "hasAddress": True,
        "hasZip": True,
        "hasPhone": True,
        "hasMail": True,
        "hasSSN": True,
        "hasDOB": True,
        "bank": "BANK",
        "base": "2026_US_Base",
        "refundable": True,
        "price": str(price),
        "key": key,
        "seller_id": str(update.effective_user.id),
        "full_info": ""
    }
    
    shop_products.append(product_entry)
    
    # Save shop products
    save_shop_products(shop_products)
    
    # Build response
    masked_display = bin_str + "**********"
    key_source = "Provided" if provided_key else "Auto-generated"
    response = f"""✅ **Stock Added Successfully!**

📦 **Product Details:**
• BIN: `{bin_str}`
• Display: `{masked_display}`
• Price: **${price:.2f}**
• Brand: {brand}

🔑 **Key:** `{key}` ({key_source})

📊 Total stock: {len(shop_products)} products"""
    
    user = update.effective_user
    log_action(user.id, user.first_name, "ADD_STOCK", f"Added BIN {bin_str} at ${price:.2f} with key {key}")
    
    await update.message.reply_text(response, parse_mode='Markdown')

@admin_only
async def remove_stock_slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove stock by slot numbers. Supports /removestockslot 1,2 or /removestockslot1."""
    text = (update.message.text or "").strip()
    raw_input = ""

    # Standard command usage: /removestockslot 1 2 3
    if context.args:
        raw_input = " ".join(context.args)
    # Compact usage: /removestockslot1 or /removestockslot1,2,3
    elif text.lower().startswith("/removestockslot"):
        raw_input = text[len("/removestockslot"):].strip()

    slot_numbers = [int(x) for x in re.findall(r"\d+", raw_input)]
    if not slot_numbers:
        await update.message.reply_text(
            "❌ Usage:\n"
            "/removestockslot 1\n"
            "/removestockslot 1 2 3\n"
            "/removestockslot 1,2,3\n"
            "/removestockslot1",
            parse_mode='Markdown'
        )
        return

    removed_entries, invalid_slots = remove_shop_products_by_slots(slot_numbers)
    removed_count = len(removed_entries)

    if removed_count == 0:
        invalid_text = f"\n⚠️ Invalid slots: {', '.join(map(str, invalid_slots))}" if invalid_slots else ""
        await update.message.reply_text(f"❌ No stock removed.{invalid_text}")
        return

    removed_preview = []
    for entry in removed_entries[:10]:
        product = entry.get("product", {})
        slot = entry.get("slot")
        bin_str = product.get("bin", "UNKNOWN")
        price = product.get("price", "0")
        removed_preview.append(f"• Slot {slot}: `{bin_str}**********` - ${price}")

    more_line = f"\n...and {removed_count - 10} more." if removed_count > 10 else ""
    invalid_line = f"\n⚠️ Invalid slots: {', '.join(map(str, invalid_slots))}" if invalid_slots else ""

    user = update.effective_user
    log_action(user.id, user.first_name, "REMOVE_STOCK_SLOT", f"Removed {removed_count} by slots: {slot_numbers}")

    await update.message.reply_text(
        f"✅ Removed {removed_count} product(s) from stock.\n"
        f"{chr(10).join(removed_preview)}"
        f"{more_line}"
        f"{invalid_line}",
        parse_mode='Markdown'
    )

@admin_only
async def confirm_pending_sendout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Publish pending deepseek/HTML bin tool stock to website shop."""
    added = apply_pending_deepseek_to_shop()
    user = update.effective_user
    if added == 0:
        await update.message.reply_text("ℹ️ Nothing pending to publish. Sync from the HTML tool, then tap **Sendout**, then run /confirm again.", parse_mode="Markdown")
        return
    log_action(user.id, user.first_name, "CONFIRM_SENDOUT", f"Published {added} product(s) from HTML tool")
    await update.message.reply_text(
        f"✅ **Published to website:** {added} listing(s) added to shop.\n"
        f"🌐 Products use **BIN only** (first 6 digits); full pipe row is in seller `full_info`.",
        parse_mode="Markdown",
    )

@admin_only
async def cancel_pending_sendout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Drop pending HTML-tool stock without publishing."""
    with PENDING_DEEPSEEK_LOCK:
        save_pending_deepseek({"groups": {}})
    user = update.effective_user
    log_action(user.id, user.first_name, "CANCEL_SENDOUT", "Cleared pending HTML tool stock")
    await update.message.reply_text("❎ Pending HTML-tool stock cleared. Website unchanged.")

@admin_only
async def clear_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Request confirmation before clearing all stock."""
    confirm_keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes, clear all stock", callback_data="stock_clear_confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data="stock_clear_cancel")
        ]
    ])
    await update.message.reply_text(
        "⚠️ **Confirm Stock Clear**\n\nThis will remove all products from the website shop.",
        parse_mode='Markdown',
        reply_markup=confirm_keyboard
    )

async def handle_stock_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline stock actions from Telegram buttons."""
    query = update.callback_query
    if not query:
        return

    user = update.effective_user
    if user.id not in ADMIN_IDS:
        await query.answer("Unauthorized", show_alert=True)
        return

    await query.answer()
    data = query.data or ""

    if data == "stock_clear_prompt":
        confirm_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Yes, clear all stock", callback_data="stock_clear_confirm"),
                InlineKeyboardButton("❌ Cancel", callback_data="stock_clear_cancel")
            ]
        ])
        await query.edit_message_text(
            "⚠️ **Confirm Stock Clear**\n\nThis will remove all products from the website shop.",
            parse_mode='Markdown',
            reply_markup=confirm_keyboard
        )
        return

    if data == "stock_clear_cancel":
        await query.edit_message_text("❎ Stock clear cancelled.")
        return

    if data == "stock_clear_confirm":
        removed_count = clear_shop_products()
        log_action(user.id, user.first_name, "CLEAR_STOCK", f"Cleared {removed_count} products")
        await query.edit_message_text(
            f"✅ Stock cleared.\n🗑️ Removed {removed_count} products from shop.\n🌐 Website products now sync from this cleared stock."
        )
        return

# ==================== BOT THREAD ====================
def run_bot():
    """Run the Telegram bot in a separate thread"""
    async def main():
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", start))
        application.add_handler(CommandHandler("balance", view_balance))
        application.add_handler(CommandHandler("setbalance", set_balance))
        application.add_handler(CommandHandler("addbalance", add_balance))
        application.add_handler(CommandHandler("removebalance", remove_balance))
        application.add_handler(CommandHandler("users", list_users))
        application.add_handler(CommandHandler("addadmin", add_admin))
        application.add_handler(CommandHandler("removeadmin", remove_admin))
        application.add_handler(CommandHandler("admins", list_admins))
        application.add_handler(CommandHandler("stock", add_stock))
        application.add_handler(CommandHandler("removestockslot", remove_stock_slot))
        application.add_handler(MessageHandler(filters.Regex(r"^/removestockslot\d"), remove_stock_slot))
        application.add_handler(CommandHandler("clearstock", clear_stock))
        application.add_handler(CommandHandler("confirm", confirm_pending_sendout))
        application.add_handler(CommandHandler("cancel_sendout", cancel_pending_sendout))
        application.add_handler(CallbackQueryHandler(handle_stock_callback, pattern="^stock_clear_(prompt|confirm|cancel)$"))
        
        logger.info("Bot started with polling...")
        await application.initialize()
        await application.start()
        await application.updater.start_polling(drop_pending_updates=True)
        
        # Keep running
        while True:
            await asyncio.sleep(3600)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())

# ==================== MAIN ====================
ensure_data_dir()

# Start bot in background thread when module loads (works with gunicorn)
# Use a flag to prevent multiple starts
_bot_started = False
def start_bot_once():
    global _bot_started
    if not _bot_started:
        _bot_started = True
        bot_thread = threading.Thread(target=run_bot, daemon=True)
        bot_thread.start()
        logger.info("Telegram bot thread started")

start_bot_once()

if __name__ == "__main__":
    # Run Flask server (for local development)
    logger.info(f"Starting PLUXO API Server on port {PORT}...")
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)

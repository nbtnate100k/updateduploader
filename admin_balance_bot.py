"""
PLUXO Admin Balance Bot
Secure Telegram bot for balance management and purchase tracking
Owner: @Xeeznk (ID: 7173346586)

Deployment: Railway + GitHub
"""

import json
import os
import logging
import threading
import asyncio
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters

# ==================== CONFIGURATION ====================
# Use environment variable for token (Railway), fallback to hardcoded for local dev
BOT_TOKEN = os.getenv("BOT_TOKEN", "8282351962:AAFXNMRR2_0Y1z4lTkvwwD_9EXzINPVI538")
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

# System state
SYSTEM_LOCKED = False

# ==================== LOGGING ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== ENSURE DATA DIRECTORY ====================
def ensure_data_dir():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)

# ==================== DATA MANAGEMENT ====================
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
    admins.add(OWNER_ID)  # Owner always admin
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
    # Keep last 1000 logs
    logs["logs"] = logs["logs"][-1000:]
    save_json(LOGS_FILE, logs)

# ==================== ADMIN SET (LOADED AT START) ====================
ADMIN_IDS = load_admins()

# ==================== SECURITY DECORATORS ====================
def admin_only(func):
    """Decorator: Only admins can use this command"""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            return  # Silently ignore non-admins
        return await func(update, context)
    return wrapper

def owner_only(func):
    """Decorator: Only owner can use this command"""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != OWNER_ID:
            return  # Silently ignore non-owner
        return await func(update, context)
    return wrapper

def check_lockdown(func):
    """Decorator: Check if system is locked"""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        global SYSTEM_LOCKED
        if SYSTEM_LOCKED and update.effective_user.id != OWNER_ID:
            await update.message.reply_text("⛔ System is locked. Contact owner.")
            return
        return await func(update, context)
    return wrapper

# ==================== COMMAND HANDLERS ====================

@admin_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command - show help menu"""
    user = update.effective_user
    is_owner = user.id == OWNER_ID
    
    msg = f"""
🔐 **PLUXO Admin Bot**
━━━━━━━━━━━━━━━━━━
👤 Welcome, {user.first_name}
🆔 Your ID: `{user.id}`
👑 Role: {"OWNER" if is_owner else "ADMIN"}

📋 **Commands:**

💰 **Balance Management:**
/balance <username> - View user balance
/setbalance <username> <amount> - Set balance
/addbalance <username> <amount> - Add to balance
/removebalance <username> <amount> - Remove from balance
/allbalances - View all balances
/users - List all registered users

🛒 **Purchase Tracking:**
/addpurchase <username> <item> <amount> - Log purchase
/purchases <username> - View user purchases
/recentpurchases - Recent purchases (last 20)

👥 **Admin Management (Owner only):**
/addadmin <user_id> - Add admin
/removeadmin <user_id> - Remove admin
/admins - List all admins

🔒 **System:**
/lockdown - Lock/unlock system (Owner only)
/logs - View recent action logs
/status - System status
"""
    await update.message.reply_text(msg, parse_mode='Markdown')
    log_action(user.id, user.first_name, "START", "Accessed bot")

@admin_only
@check_lockdown
async def view_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View a user's balance"""
    if not context.args:
        await update.message.reply_text("❌ Usage: /balance <username>")
        return
    
    username = context.args[0].lower().strip().lstrip('@')
    balances = load_balances()
    
    if username not in balances:
        await update.message.reply_text(f"❌ User `{username}` not found.", parse_mode='Markdown')
        return
    
    user_data = balances[username]
    balance = user_data.get("balance", 0)
    total_recharge = user_data.get("totalRecharge", 0)
    
    msg = f"""
💰 **Balance Info**
━━━━━━━━━━━━━━━━━━
👤 Username: `{username}`
💵 Balance: **${balance:.2f}**
📊 Total Recharged: **${total_recharge:.2f}**
"""
    await update.message.reply_text(msg, parse_mode='Markdown')

@admin_only
@check_lockdown
async def set_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set a user's balance"""
    if len(context.args) < 2:
        await update.message.reply_text("❌ Usage: /setbalance <username> <amount>")
        return
    
    username = context.args[0].lower().strip().lstrip('@')
    try:
        amount = float(context.args[1].replace('$', '').replace(',', ''))
    except ValueError:
        await update.message.reply_text("❌ Invalid amount.")
        return
    
    balances = load_balances()
    
    if username not in balances:
        balances[username] = {"balance": 0, "totalRecharge": 0}
    
    old_balance = balances[username].get("balance", 0)
    balances[username]["balance"] = amount
    save_balances(balances)
    
    user = update.effective_user
    log_action(user.id, user.first_name, "SET_BALANCE", f"{username}: ${old_balance:.2f} → ${amount:.2f}")
    
    await update.message.reply_text(f"""
✅ **Balance Updated**
━━━━━━━━━━━━━━━━━━
👤 User: `{username}`
📉 Old: **${old_balance:.2f}**
📈 New: **${amount:.2f}**
""", parse_mode='Markdown')

@admin_only
@check_lockdown
async def add_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add to a user's balance"""
    if len(context.args) < 2:
        await update.message.reply_text("❌ Usage: /addbalance <username> <amount>")
        return
    
    username = context.args[0].lower().strip().lstrip('@')
    try:
        amount = float(context.args[1].replace('$', '').replace(',', ''))
        if amount <= 0:
            await update.message.reply_text("❌ Amount must be positive.")
            return
    except ValueError:
        await update.message.reply_text("❌ Invalid amount.")
        return
    
    balances = load_balances()
    
    if username not in balances:
        balances[username] = {"balance": 0, "totalRecharge": 0}
    
    old_balance = balances[username].get("balance", 0)
    new_balance = old_balance + amount
    balances[username]["balance"] = new_balance
    balances[username]["totalRecharge"] = balances[username].get("totalRecharge", 0) + amount
    save_balances(balances)
    
    user = update.effective_user
    log_action(user.id, user.first_name, "ADD_BALANCE", f"{username}: +${amount:.2f} (${old_balance:.2f} → ${new_balance:.2f})")
    
    await update.message.reply_text(f"""
✅ **Balance Added**
━━━━━━━━━━━━━━━━━━
👤 User: `{username}`
➕ Added: **+${amount:.2f}**
💰 New Balance: **${new_balance:.2f}**
""", parse_mode='Markdown')

@admin_only
@check_lockdown
async def remove_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove from a user's balance"""
    if len(context.args) < 2:
        await update.message.reply_text("❌ Usage: /removebalance <username> <amount>")
        return
    
    username = context.args[0].lower().strip().lstrip('@')
    try:
        amount = float(context.args[1].replace('$', '').replace(',', ''))
        if amount <= 0:
            await update.message.reply_text("❌ Amount must be positive.")
            return
    except ValueError:
        await update.message.reply_text("❌ Invalid amount.")
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
    log_action(user.id, user.first_name, "REMOVE_BALANCE", f"{username}: -${amount:.2f} (${old_balance:.2f} → ${new_balance:.2f})")
    
    await update.message.reply_text(f"""
✅ **Balance Removed**
━━━━━━━━━━━━━━━━━━
👤 User: `{username}`
➖ Removed: **-${amount:.2f}**
💰 New Balance: **${new_balance:.2f}**
""", parse_mode='Markdown')

@admin_only
@check_lockdown
async def all_balances(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View all user balances"""
    balances = load_balances()
    
    if not balances:
        await update.message.reply_text("📭 No users found.")
        return
    
    # Sort by balance descending
    sorted_users = sorted(balances.items(), key=lambda x: x[1].get("balance", 0), reverse=True)
    
    msg = "💰 **All User Balances**\n━━━━━━━━━━━━━━━━━━\n"
    total = 0
    
    for i, (username, data) in enumerate(sorted_users[:50], 1):  # Top 50
        balance = data.get("balance", 0)
        total += balance
        msg += f"{i}. `{username}`: **${balance:.2f}**\n"
    
    msg += f"\n━━━━━━━━━━━━━━━━━━\n💵 **Total**: **${total:.2f}**"
    
    if len(sorted_users) > 50:
        msg += f"\n_(Showing top 50 of {len(sorted_users)} users)_"
    
    await update.message.reply_text(msg, parse_mode='Markdown')

@admin_only
@check_lockdown
async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all registered users"""
    balances = load_balances()
    
    if not balances:
        await update.message.reply_text("📭 No users registered yet.")
        return
    
    # Sort by registration date (newest first)
    sorted_users = sorted(
        balances.items(), 
        key=lambda x: x[1].get("registeredAt", "2000-01-01"), 
        reverse=True
    )
    
    msg = "👥 **Registered Users**\n━━━━━━━━━━━━━━━━━━\n"
    
    for i, (username, data) in enumerate(sorted_users[:50], 1):
        balance = data.get("balance", 0)
        registered = data.get("registeredAt", "")
        if registered:
            try:
                dt = datetime.fromisoformat(registered).strftime('%m/%d/%y')
            except:
                dt = "N/A"
        else:
            dt = "N/A"
        msg += f"{i}. `{username}` - ${balance:.2f} ({dt})\n"
    
    msg += f"\n━━━━━━━━━━━━━━━━━━\n📊 **Total Users**: {len(balances)}"
    
    await update.message.reply_text(msg, parse_mode='Markdown')

@admin_only
@check_lockdown
async def add_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log a purchase"""
    if len(context.args) < 3:
        await update.message.reply_text("❌ Usage: /addpurchase <username> <item_name> <amount>")
        return
    
    username = context.args[0].lower().strip().lstrip('@')
    item_name = context.args[1]
    try:
        amount = float(context.args[2])
    except ValueError:
        await update.message.reply_text("❌ Invalid amount.")
        return
    
    purchases = load_purchases()
    
    purchase_entry = {
        "id": len(purchases.get("purchases", [])) + 1,
        "username": username,
        "item": item_name,
        "amount": amount,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "logged_by": update.effective_user.id
    }
    
    purchases["purchases"].append(purchase_entry)
    save_purchases(purchases)
    
    user = update.effective_user
    log_action(user.id, user.first_name, "ADD_PURCHASE", f"{username}: {item_name} (${amount:.2f})")
    
    await update.message.reply_text(f"""
✅ **Purchase Logged**
━━━━━━━━━━━━━━━━━━
👤 User: `{username}`
📦 Item: **{item_name}**
💵 Amount: **${amount:.2f}**
🕐 Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
""", parse_mode='Markdown')

@admin_only
@check_lockdown
async def view_purchases(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View a user's purchases"""
    if not context.args:
        await update.message.reply_text("❌ Usage: /purchases <username>")
        return
    
    username = context.args[0].lower().strip().lstrip('@')
    purchases = load_purchases()
    
    user_purchases = [p for p in purchases.get("purchases", []) if p["username"] == username]
    
    if not user_purchases:
        await update.message.reply_text(f"📭 No purchases found for `{username}`.", parse_mode='Markdown')
        return
    
    msg = f"🛒 **Purchases for** `{username}`\n━━━━━━━━━━━━━━━━━━\n"
    total = 0
    
    for p in user_purchases[-20:]:  # Last 20
        dt = datetime.fromisoformat(p["timestamp"]).strftime('%m/%d %H:%M')
        msg += f"• {p['item']} - **${p['amount']:.2f}** ({dt})\n"
        total += p["amount"]
    
    msg += f"\n━━━━━━━━━━━━━━━━━━\n💵 **Total Spent**: **${total:.2f}**"
    
    await update.message.reply_text(msg, parse_mode='Markdown')

@admin_only
@check_lockdown
async def recent_purchases(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View recent purchases"""
    purchases = load_purchases()
    all_purchases = purchases.get("purchases", [])
    
    if not all_purchases:
        await update.message.reply_text("📭 No purchases recorded.")
        return
    
    msg = "🛒 **Recent Purchases**\n━━━━━━━━━━━━━━━━━━\n"
    
    for p in all_purchases[-20:][::-1]:  # Last 20, newest first
        dt = datetime.fromisoformat(p["timestamp"]).strftime('%m/%d %H:%M')
        msg += f"• `{p['username']}`: {p['item']} - **${p['amount']:.2f}** ({dt})\n"
    
    await update.message.reply_text(msg, parse_mode='Markdown')

# ==================== ADMIN MANAGEMENT (OWNER ONLY) ====================

@owner_only
async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a new admin (Owner only)"""
    global ADMIN_IDS
    
    if not context.args:
        await update.message.reply_text("❌ Usage: /addadmin <user_id>")
        return
    
    try:
        new_admin_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return
    
    if new_admin_id in ADMIN_IDS:
        await update.message.reply_text("⚠️ User is already an admin.")
        return
    
    ADMIN_IDS.add(new_admin_id)
    save_admins(ADMIN_IDS)
    
    log_action(OWNER_ID, "OWNER", "ADD_ADMIN", f"Added admin: {new_admin_id}")
    
    await update.message.reply_text(f"""
✅ **Admin Added**
━━━━━━━━━━━━━━━━━━
🆔 User ID: `{new_admin_id}`
👥 Total Admins: {len(ADMIN_IDS)}
""", parse_mode='Markdown')

@owner_only
async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove an admin (Owner only)"""
    global ADMIN_IDS
    
    if not context.args:
        await update.message.reply_text("❌ Usage: /removeadmin <user_id>")
        return
    
    try:
        admin_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return
    
    if admin_id == OWNER_ID:
        await update.message.reply_text("⛔ Cannot remove owner.")
        return
    
    if admin_id not in ADMIN_IDS:
        await update.message.reply_text("⚠️ User is not an admin.")
        return
    
    ADMIN_IDS.discard(admin_id)
    save_admins(ADMIN_IDS)
    
    log_action(OWNER_ID, "OWNER", "REMOVE_ADMIN", f"Removed admin: {admin_id}")
    
    await update.message.reply_text(f"""
✅ **Admin Removed**
━━━━━━━━━━━━━━━━━━
🆔 User ID: `{admin_id}`
👥 Total Admins: {len(ADMIN_IDS)}
""", parse_mode='Markdown')

@owner_only
async def list_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all admins"""
    msg = "👥 **Admin List**\n━━━━━━━━━━━━━━━━━━\n"
    
    for i, admin_id in enumerate(ADMIN_IDS, 1):
        role = "👑 OWNER" if admin_id == OWNER_ID else "🔑 Admin"
        msg += f"{i}. `{admin_id}` - {role}\n"
    
    await update.message.reply_text(msg, parse_mode='Markdown')

# ==================== SYSTEM COMMANDS ====================

@owner_only
async def lockdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle system lockdown (Owner only)"""
    global SYSTEM_LOCKED
    
    SYSTEM_LOCKED = not SYSTEM_LOCKED
    status = "🔒 LOCKED" if SYSTEM_LOCKED else "🔓 UNLOCKED"
    
    log_action(OWNER_ID, "OWNER", "LOCKDOWN", status)
    
    await update.message.reply_text(f"""
⚙️ **System Status Changed**
━━━━━━━━━━━━━━━━━━
Status: **{status}**
{"⛔ All admin commands frozen" if SYSTEM_LOCKED else "✅ All commands active"}
""", parse_mode='Markdown')

@admin_only
async def view_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View recent action logs"""
    logs = load_json(LOGS_FILE, {"logs": []})
    log_entries = logs.get("logs", [])
    
    if not log_entries:
        await update.message.reply_text("📭 No logs found.")
        return
    
    msg = "📋 **Recent Logs**\n━━━━━━━━━━━━━━━━━━\n"
    
    for log in log_entries[-15:][::-1]:  # Last 15, newest first
        dt = datetime.fromisoformat(log["timestamp"]).strftime('%m/%d %H:%M')
        msg += f"• [{dt}] **{log['action']}** by {log['admin_name']}\n"
        if log.get("details"):
            msg += f"  _{log['details']}_\n"
    
    await update.message.reply_text(msg, parse_mode='Markdown')

@admin_only
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View system status"""
    balances = load_balances()
    purchases = load_purchases()
    
    total_balance = sum(u.get("balance", 0) for u in balances.values())
    total_users = len(balances)
    total_purchases = len(purchases.get("purchases", []))
    
    lock_status = "🔒 LOCKED" if SYSTEM_LOCKED else "🔓 ACTIVE"
    
    msg = f"""
⚙️ **System Status**
━━━━━━━━━━━━━━━━━━
🔐 Status: **{lock_status}**
👥 Admins: **{len(ADMIN_IDS)}**
👤 Users: **{total_users}**
💰 Total Balance: **${total_balance:.2f}**
🛒 Purchases: **{total_purchases}**
"""
    await update.message.reply_text(msg, parse_mode='Markdown')

# ==================== MAIN ====================
# Flask app for webhooks
flask_app = Flask(__name__)
bot_instance = None

# Function to register new user (called from webhook)
def register_new_user(username, email=None):
    """Register a new user in the bot's database"""
    username = username.lower().strip()
    balances = load_balances()
    
    if username not in balances:
        balances[username] = {
            "balance": 0,
            "totalRecharge": 0,
            "email": email,
            "registeredAt": datetime.now(timezone.utc).isoformat()
        }
        save_balances(balances)
        log_action(0, "SYSTEM", "NEW_USER", f"User registered: {username}")
        return True
    return False

# Send notification to owner
async def notify_owner_new_user(username, email):
    """Send notification to owner about new registration"""
    try:
        bot = Bot(token=BOT_TOKEN)
        msg = f"""
🆕 **New User Registered**
━━━━━━━━━━━━━━━━━━
👤 Username: `{username}`
📧 Email: `{email or 'N/A'}`
🕐 Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
"""
        await bot.send_message(chat_id=OWNER_ID, text=msg, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Failed to notify owner: {e}")

# Webhook endpoint for user registration
@flask_app.route('/api/register', methods=['POST'])
def webhook_register():
    """Receive new user registrations from the website"""
    try:
        # Verify secret
        secret = request.headers.get('X-Webhook-Secret', '')
        if secret != WEBHOOK_SECRET:
            return jsonify({"error": "Unauthorized"}), 401
        
        data = request.json
        username = data.get('username', '').lower().strip()
        email = data.get('email', '')
        
        if not username:
            return jsonify({"error": "Username required"}), 400
        
        # Register the user
        is_new = register_new_user(username, email)
        
        # Notify owner asynchronously
        if is_new:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(notify_owner_new_user(username, email))
                loop.close()
            except Exception as e:
                logger.error(f"Notification error: {e}")
        
        return jsonify({
            "success": True,
            "username": username,
            "isNew": is_new
        })
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"error": str(e)}), 500

# API endpoint to get user balance (for website)
@flask_app.route('/api/balance/<username>', methods=['GET'])
def get_user_balance(username):
    """Get user balance - called by website"""
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
            # User not found, return 0 balance
            return jsonify({
                "success": True,
                "username": username,
                "balance": 0,
                "totalRecharge": 0
            })
    except Exception as e:
        logger.error(f"Balance API error: {e}")
        return jsonify({"error": str(e)}), 500

# API endpoint to update balance (for purchases on website)
@flask_app.route('/api/balance/update', methods=['POST'])
def update_user_balance():
    """Update user balance - called by website for purchases"""
    try:
        secret = request.headers.get('X-Webhook-Secret', '')
        if secret != WEBHOOK_SECRET:
            return jsonify({"error": "Unauthorized"}), 401
        
        data = request.json
        username = data.get('username', '').lower().strip()
        action = data.get('action', '')  # 'subtract' or 'add'
        amount = float(data.get('amount', 0))
        reason = data.get('reason', '')
        
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
        
        log_action(0, "WEBSITE", f"BALANCE_{action.upper()}", f"{username}: ${amount:.2f} ({reason})")
        
        return jsonify({
            "success": True,
            "username": username,
            "oldBalance": old_balance,
            "newBalance": new_balance
        })
    except Exception as e:
        logger.error(f"Balance update error: {e}")
        return jsonify({"error": str(e)}), 500

# Health check endpoint
@flask_app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "ok", "bot": "PLUXO Admin"})

# CORS headers
@flask_app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,X-Webhook-Secret')
    response.headers.add('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
    return response

def run_flask():
    """Run Flask server in background"""
    flask_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

def main():
    """Start the bot"""
    ensure_data_dir()
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))
    
    # Balance commands
    application.add_handler(CommandHandler("balance", view_balance))
    application.add_handler(CommandHandler("setbalance", set_balance))
    application.add_handler(CommandHandler("addbalance", add_balance))
    application.add_handler(CommandHandler("removebalance", remove_balance))
    application.add_handler(CommandHandler("allbalances", all_balances))
    application.add_handler(CommandHandler("users", list_users))
    
    # Purchase commands
    application.add_handler(CommandHandler("addpurchase", add_purchase))
    application.add_handler(CommandHandler("purchases", view_purchases))
    application.add_handler(CommandHandler("recentpurchases", recent_purchases))
    
    # Admin management (Owner only)
    application.add_handler(CommandHandler("addadmin", add_admin))
    application.add_handler(CommandHandler("removeadmin", remove_admin))
    application.add_handler(CommandHandler("admins", list_admins))
    
    # System commands
    application.add_handler(CommandHandler("lockdown", lockdown))
    application.add_handler(CommandHandler("logs", view_logs))
    application.add_handler(CommandHandler("status", status))
    
    # Start bot
    logger.info("🤖 Bot starting...")
    logger.info(f"👑 Owner: {OWNER_ID}")
    logger.info(f"👥 Admins loaded: {len(ADMIN_IDS)}")
    
    # Start Flask in background thread for webhooks
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info(f"🌐 Webhook server started on port {PORT}")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

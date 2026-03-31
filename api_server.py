"""
PLUXO API Server
Handles balance and user data for the website
"""

import json
import os
import logging
from datetime import datetime, timezone
from flask import Flask, request, jsonify
from flask_cors import CORS

# ==================== CONFIGURATION ====================
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "pluxo_secret_2024")
PORT = int(os.getenv("PORT", 5000))
OWNER_ID = 7173346586

# Data files (shared with bot)
DATA_DIR = "bot_data"
BALANCES_FILE = os.path.join(DATA_DIR, "balances.json")
LOGS_FILE = os.path.join(DATA_DIR, "action_logs.json")

# ==================== LOGGING ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== FLASK APP ====================
app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

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

def load_balances():
    return load_json(BALANCES_FILE, {})

def save_balances(balances):
    save_json(BALANCES_FILE, balances)

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

# ==================== API ENDPOINTS ====================

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "ok", "server": "PLUXO API"})

@app.route('/api/register', methods=['POST', 'OPTIONS'])
def webhook_register():
    """Receive new user registrations from the website"""
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        secret = request.headers.get('X-Webhook-Secret', '')
        if secret != WEBHOOK_SECRET:
            logger.warning(f"Unauthorized register attempt")
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
        
        return jsonify({
            "success": True,
            "username": username,
            "isNew": is_new
        })
    except Exception as e:
        logger.error(f"Register error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/balance/<username>', methods=['GET', 'OPTIONS'])
def get_user_balance(username):
    """Get user balance"""
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
            return jsonify({
                "success": True,
                "username": username,
                "balance": 0,
                "totalRecharge": 0
            })
    except Exception as e:
        logger.error(f"Balance API error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/balance/update', methods=['POST', 'OPTIONS'])
def update_user_balance():
    """Update user balance"""
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

# ==================== MAIN ====================
if __name__ == "__main__":
    ensure_data_dir()
    logger.info(f"Starting PLUXO API Server on port {PORT}...")
    app.run(host='0.0.0.0', port=PORT, debug=False)

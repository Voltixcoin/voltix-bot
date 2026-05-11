import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from flask import Flask
import telebot
from telebot import types

# =========================
# CONFIG
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN", "PUT_YOUR_BOT_TOKEN_HERE")

ADMIN_IDS = {6828876093, 7434864137}

CHANNEL_USERNAME = "VOLTIXVTXCoin"
CHANNEL_LINK = "https://t.me/voltIXVTX"
BOT_USERNAME = "VoltIXVTX_bot"

CAMPAIGN_DAYS = 15
MIN_VALID_INVITES = 25

PRIZES = {
    1: "$10",
    2: "$5",
    3: "$3",
}

DB_PATH = "voltix.db"

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# =========================
# DATABASE
# =========================

def db():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    con = db()
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        inviter_id INTEGER,
        banned INTEGER DEFAULT 0,
        joined_at TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        inviter_id INTEGER NOT NULL,
        invited_id INTEGER NOT NULL UNIQUE,
        created_at TEXT NOT NULL
    )
    """)

    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('campaign_active', '0')")
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('campaign_start', '')")
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('campaign_end', '')")

    con.commit()
    con.close()


def get_setting(key, default=""):
    con = db()
    cur = con.cursor()
    row = cur.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    con.close()
    return row[0] if row else default


def set_setting(key, value):
    con = db()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    con.commit()
    con.close()


def save_user(message, inviter_id=None):
    user = message.from_user
    now = datetime.now(timezone.utc).isoformat()

    con = db()
    cur = con.cursor()

    exists = cur.execute("SELECT user_id FROM users WHERE user_id=?", (user.id,)).fetchone()

    if exists:
        cur.execute(
            "UPDATE users SET username=?, first_name=? WHERE user_id=?",
            (user.username, user.first_name, user.id),
        )
        con.commit()
        con.close()
        return

    cur.execute(
        "INSERT INTO users (user_id, username, first_name, inviter_id, joined_at) VALUES (?, ?, ?, ?, ?)",
        (user.id, user.username, user.first_name, inviter_id, now),
    )

    if inviter_id and inviter_id != user.id:
        inviter_exists = cur.execute("SELECT user_id FROM users WHERE user_id=?", (inviter_id,)).fetchone()
        if inviter_exists:
            cur.execute(
                "INSERT OR IGNORE INTO referrals (inviter_id, invited_id, created_at) VALUES (?, ?, ?)",
                (inviter_id, user.id, now),
            )

    con.commit()
    con.close()


def raw_invites(user_id):
    con = db()
    cur = con.cursor()
    count = cur.execute("SELECT COUNT(*) FROM referrals WHERE inviter_id=?", (user_id,)).fetchone()[0]
    con.close()
    return count


def total_users():
    con = db()
    cur = con.cursor()
    count = cur.execute("SELECT COUNT(*) FROM users WHERE banned=0").fetchone()[0]
    con.close()
    return count


def user_list():
    con = db()
    cur = con.cursor()
    rows = cur.execute("SELECT user_id FROM users WHERE banned=0").fetchall()
    con.close()
    return rows


def referral_rows():
    con = db()
    cur = con.cursor()
    rows = cur.execute("""
        SELECT r.inviter_id, r.invited_id, u.username, u.first_name
        FROM referrals r
        LEFT JOIN users u ON u.user_id = r.inviter_id
    """).fetchall()
    con.close()
    return rows


def ban_user(user_id):
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE users SET banned=1 WHERE user_id=?", (user_id,))
    con.commit()
    con.close()


def reset_campaign_data():
    con = db()
    cur = con.cursor()
    cur.execute("DELETE FROM referrals")
    cur.execute("UPDATE users SET inviter_id=NULL")
    con.commit()
    con.close()

    set_setting("campaign_active", "0")
    set_setting("campaign_start", "")
    set_setting("campaign_end", "")


# =========================
# HELPERS
# =========================

def is_admin(user_id):
    return user_id in ADMIN_IDS


def campaign_active():
    return get_setting("campaign_active", "0") == "1"


def parse_inviter(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return None

    try:
        inviter_id = int(parts[1])
        if inviter_id == message.from_user.id:
            return None
        return inviter_id
    except Exception:
        return None


def is_channel_member(user_id):
    try:
        member = bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception:
        return False


def user_keyboard(invite_link):
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK))
    keyboard.add(types.InlineKeyboardButton("🚀 Share Invite Link", switch_inline_query=f"Join VoltIX VTX and win rewards: {invite_link}"))
    return keyboard


def admin_keyboard():
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("▶️ Start Campaign", callback_data="admin_start"))
    keyboard.add(types.InlineKeyboardButton("⏹ End Campaign", callback_data="admin_end"))
    keyboard.add(types.InlineKeyboardButton("🏆 Winners", callback_data="admin_winners"))
    keyboard.add(types.InlineKeyboardButton("📊 Stats", callback_data="admin_stats"))
    return keyboard


def calculate_valid_invites():
    scores = {}

    for inviter_id, invited_id, username, first_name in referral_rows():
        if is_channel_member(invited_id):
            if inviter_id not in scores:
                scores[inviter_id] = {
                    "user_id": inviter_id,
                    "username": username,
                    "first_name": first_name,
                    "valid_invites": 0,
                }
            scores[inviter_id]["valid_invites"] += 1

    return sorted(scores.values(), key=lambda x: x["valid_invites"], reverse=True)


def build_winners_text():
    ranked = calculate_valid_invites()
    qualified = [u for u in ranked if u["valid_invites"] >= MIN_VALID_INVITES]
    winners = qualified[:3]

    if not winners:
        return (
            "🏆 <b>Campaign Winners</b>\n\n"
            "No qualified winners yet.\n"
            f"Minimum required valid invites: <b>{MIN_VALID_INVITES}</b>"
        )

    text = "🏆 <b>Campaign Winners</b>\n\n"

    for index, user in enumerate(winners, start=1):
        name = f"@{user['username']}" if user.get("username") else user.get("first_name") or str(user["user_id"])
        text += f"{index}. {name} — {user['valid_invites']} valid invites — Prize: <b>{PRIZES[index]}</b>\n"

    text += "\n✅ Only invited users still subscribed to the channel are counted."
    return text


# =========================
# USER COMMANDS
# =========================

@bot.message_handler(commands=["start"])
def start(message):
    inviter_id = parse_inviter(message)
    save_user(message, inviter_id)

    invite_link = f"https://t.me/{BOT_USERNAME}?start={message.from_user.id}"
    invites = raw_invites(message.from_user.id)
    remaining = max(0, MIN_VALID_INVITES - invites)

    joined = is_channel_member(message.from_user.id)
    join_status = "✅ Joined" if joined else "❌ Not joined yet"
    status = "Active" if campaign_active() else "Not active yet"

    text = f"""
🎉 <b>Welcome to VoltIX VTX Referral Campaign</b>

📢 Channel: {CHANNEL_USERNAME}
👤 Your channel status: <b>{join_status}</b>
📅 Campaign status: <b>{status}</b>

🏆 <b>Rewards</b>
🥇 1st place: {PRIZES[1]}
🥈 2nd place: {PRIZES[2]}
🥉 3rd place: {PRIZES[3]}

⏳ Campaign duration: <b>{CAMPAIGN_DAYS} days</b>
🎯 Minimum to qualify: <b>{MIN_VALID_INVITES}</b> valid invites

🔗 <b>Your invite link:</b>
<code>{invite_link}</code>

📊 Recorded invites: <b>{invites}</b>
🎯 Remaining to qualify: <b>{remaining}</b>

⚠️ Important: An invite is valid only if the invited person stays subscribed until the campaign ends.
"""

    bot.send_message(message.chat.id, text, reply_markup=user_keyboard(invite_link))


@bot.message_handler(commands=["help"])
def help_cmd(message):
    bot.send_message(message.chat.id, """
<b>Commands</b>

/start - Get your invite link
/myinvites - Show your invite stats
/top - Show live leaderboard
/help - Show this help message

<b>Rules</b>
- Invite users with your personal link.
- The invited user must join the channel.
- The invited user must stay subscribed until the campaign ends.
- Minimum valid invites required: 25.
""")


@bot.message_handler(commands=["myinvites"])
def my_invites(message):
    invites = raw_invites(message.from_user.id)
    remaining = max(0, MIN_VALID_INVITES - invites)

    bot.send_message(message.chat.id, f"""
📊 <b>Your Referral Stats</b>

Recorded invites: <b>{invites}</b>
Remaining to qualify: <b>{remaining}</b>

Final valid invites are checked at campaign end based on who is still subscribed.
""")


@bot.message_handler(commands=["top"])
def top_cmd(message):
    ranked = calculate_valid_invites()

    if not ranked:
        bot.send_message(message.chat.id, "🏆 No valid referrals yet.")
        return

    text = "🏆 <b>Live Leaderboard</b>\n\n"

    for index, user in enumerate(ranked[:10], start=1):
        name = f"@{user['username']}" if user.get("username") else user.get("first_name") or str(user["user_id"])
        text += f"{index}. {name} — {user['valid_invites']} valid invites\n"

    text += "\n✅ Only users currently subscribed to the channel are counted."
    bot.send_message(message.chat.id, text)


# =========================
# ADMIN COMMANDS
# =========================

@bot.message_handler(commands=["admin"])
def admin_cmd(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "You are not allowed to use this command.")
        return

    status = "Active" if campaign_active() else "Inactive"

    text = f"""
⚙️ <b>Admin Panel</b>

Bot: @{BOT_USERNAME}
Channel: {CHANNEL_USERNAME}
Admin ID: {message.from_user.id}

Campaign status: <b>{status}</b>
Users: <b>{total_users()}</b>

Minimum valid invites: <b>{MIN_VALID_INVITES}</b>
Duration: <b>{CAMPAIGN_DAYS} days</b>

Rewards:
1st: {PRIZES[1]}
2nd: {PRIZES[2]}
3rd: {PRIZES[3]}

Admin commands:
/start_campaign
/end_campaign
/winners
/stats
/broadcast your message
/ban user_id
/reset_campaign
"""

    bot.send_message(message.chat.id, text, reply_markup=admin_keyboard())


@bot.message_handler(commands=["start_campaign"])
def start_campaign(message):
    if not is_admin(message.from_user.id):
        return

    now = datetime.now(timezone.utc)
    end = now + timedelta(days=CAMPAIGN_DAYS)

    set_setting("campaign_active", "1")
    set_setting("campaign_start", now.isoformat())
    set_setting("campaign_end", end.isoformat())

    bot.send_message(message.chat.id, f"▶️ Campaign started.\n\nEnd: <code>{end.isoformat()}</code>")


@bot.message_handler(commands=["end_campaign"])
def end_campaign(message):
    if not is_admin(message.from_user.id):
        return

    set_setting("campaign_active", "0")
    bot.send_message(message.chat.id, "⏹ Campaign ended.\n\n" + build_winners_text())


@bot.message_handler(commands=["winners"])
def winners(message):
    if not is_admin(message.from_user.id):
        return

    bot.send_message(message.chat.id, build_winners_text())


@bot.message_handler(commands=["stats"])
def stats(message):
    if not is_admin(message.from_user.id):
        return

    ranked = calculate_valid_invites()
    total_valid = sum(u["valid_invites"] for u in ranked)
    qualified = len([u for u in ranked if u["valid_invites"] >= MIN_VALID_INVITES])

    bot.send_message(message.chat.id, f"""
📊 <b>Bot Statistics</b>

Total users: <b>{total_users()}</b>
Current valid referrals: <b>{total_valid}</b>
Qualified users: <b>{qualified}</b>
""")


@bot.message_handler(commands=["broadcast"])
def broadcast(message):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Usage: /broadcast your message")
        return

    sent = 0
    failed = 0

    for (user_id,) in user_list():
        try:
            bot.send_message(user_id, parts[1])
            sent += 1
            time.sleep(0.05)
        except Exception:
            failed += 1

    bot.send_message(message.chat.id, f"Broadcast completed.\nSent: {sent}\nFailed: {failed}")


@bot.message_handler(commands=["ban"])
def ban_cmd(message):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Usage: /ban user_id")
        return

    try:
        user_id = int(parts[1])
        ban_user(user_id)
        bot.send_message(message.chat.id, f"User {user_id} has been banned.")
    except ValueError:
        bot.send_message(message.chat.id, "Invalid user ID.")


@bot.message_handler(commands=["reset_campaign"])
def reset_cmd(message):
    if not is_admin(message.from_user.id):
        return

    reset_campaign_data()
    bot.send_message(message.chat.id, "Campaign data has been reset.")


@bot.callback_query_handler(func=lambda call: True)
def callbacks(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Not allowed", show_alert=True)
        return

    if call.data == "admin_start":
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=CAMPAIGN_DAYS)
        set_setting("campaign_active", "1")
        set_setting("campaign_start", now.isoformat())
        set_setting("campaign_end", end.isoformat())
        bot.send_message(call.message.chat.id, f"▶️ Campaign started.\nEnd: <code>{end.isoformat()}</code>")

    elif call.data == "admin_end":
        set_setting("campaign_active", "0")
        bot.send_message(call.message.chat.id, "⏹ Campaign ended.\n\n" + build_winners_text())

    elif call.data == "admin_winners":
        bot.send_message(call.message.chat.id, build_winners_text())

    elif call.data == "admin_stats":
        ranked = calculate_valid_invites()
        total_valid = sum(u["valid_invites"] for u in ranked)
        bot.send_message(call.message.chat.id, f"📊 Stats\n\nUsers: {total_users()}\nValid referrals: {total_valid}")

    bot.answer_callback_query(call.id)


# =========================
# FLASK HEALTH SERVER FOR RENDER
# =========================

app = Flask(__name__)

@app.route("/")
def home():
    return "VoltIX bot is running."


def run_flask():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)


def run_bot():
    bot.remove_webhook()
    bot.infinity_polling(timeout=30, long_polling_timeout=30)


if __name__ == "__main__":
    if BOT_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
        raise RuntimeError("BOT_TOKEN is missing. Add BOT_TOKEN in Render Environment Variables.")

    init_db()

    threading.Thread(target=run_flask, daemon=True).start()
    run_bot()

import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "PUT_YOUR_BOT_TOKEN_HERE")
ADMIN_IDS = {6828876093}
CHANNEL_USERNAME = "@VOLTIXVTXCoin"
CHANNEL_LINK = "https://t.me/voltIXVTX"
BOT_USERNAME = "VoltIXVTX_bot"
CAMPAIGN_DAYS = 15
MIN_VALID_INVITES = 25
PRIZES = {1: "$10", 2: "$5", 3: "$3"}
DB_PATH = "voltix.db"

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

def connect_db():
    return sqlite3.connect(DB_PATH)

def init_db():
    with connect_db() as db:
        cur = db.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
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
        db.commit()

def get_setting(key, default=""):
    with connect_db() as db:
        row = db.cursor().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

def set_setting(key, value):
    with connect_db() as db:
        db.cursor().execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        db.commit()

def campaign_active():
    return get_setting("campaign_active", "0") == "1"

def campaign_dates():
    return get_setting("campaign_start", ""), get_setting("campaign_end", "")

def save_user(message: Message, inviter_id=None):
    user = message.from_user
    now = datetime.now(timezone.utc).isoformat()
    with connect_db() as db:
        cur = db.cursor()
        exists = cur.execute("SELECT user_id FROM users WHERE user_id=?", (user.id,)).fetchone()
        if exists:
            cur.execute("UPDATE users SET username=?, first_name=? WHERE user_id=?", (user.username, user.first_name, user.id))
            db.commit()
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
        db.commit()

def get_raw_invites(user_id):
    with connect_db() as db:
        return db.cursor().execute("SELECT COUNT(*) FROM referrals WHERE inviter_id=?", (user_id,)).fetchone()[0]

def get_total_users():
    with connect_db() as db:
        return db.cursor().execute("SELECT COUNT(*) FROM users WHERE banned=0").fetchone()[0]

def get_users():
    with connect_db() as db:
        return db.cursor().execute("SELECT user_id FROM users WHERE banned=0").fetchall()

def get_referrals():
    with connect_db() as db:
        return db.cursor().execute("""
            SELECT r.inviter_id, r.invited_id, u.username, u.first_name
            FROM referrals r
            LEFT JOIN users u ON u.user_id = r.inviter_id
        """).fetchall()

def ban_user(user_id):
    with connect_db() as db:
        db.cursor().execute("UPDATE users SET banned=1 WHERE user_id=?", (user_id,))
        db.commit()

def reset_campaign():
    with connect_db() as db:
        cur = db.cursor()
        cur.execute("DELETE FROM referrals")
        cur.execute("UPDATE users SET inviter_id=NULL")
        db.commit()
    set_setting("campaign_active", "0")
    set_setting("campaign_start", "")
    set_setting("campaign_end", "")

def is_admin(user_id):
    return user_id in ADMIN_IDS

def parse_inviter(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return None
    try:
        inviter_id = int(parts[1])
        return None if inviter_id == message.from_user.id else inviter_id
    except ValueError:
        return None

async def is_channel_member(user_id):
    try:
        member = await bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}
    except Exception as exc:
        logging.warning("Could not check channel member %s: %s", user_id, exc)
        return False

def user_keyboard(invite_link):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Join Channel", url=CHANNEL_LINK)],
        [InlineKeyboardButton(text="Share Invite Link", switch_inline_query=f"Join VoltIX VTX and win rewards: {invite_link}")],
    ])

def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Start Campaign", callback_data="admin_start")],
        [InlineKeyboardButton(text="End Campaign", callback_data="admin_end")],
        [InlineKeyboardButton(text="Winners", callback_data="admin_winners")],
        [InlineKeyboardButton(text="Stats", callback_data="admin_stats")],
    ])

async def calculate_valid_invites():
    scores = {}
    for inviter_id, invited_id, username, first_name in get_referrals():
        if await is_channel_member(invited_id):
            if inviter_id not in scores:
                scores[inviter_id] = {"user_id": inviter_id, "username": username, "first_name": first_name, "valid_invites": 0}
            scores[inviter_id]["valid_invites"] += 1
    return sorted(scores.values(), key=lambda row: row["valid_invites"], reverse=True)

async def winners_text():
    ranked = await calculate_valid_invites()
    qualified = [u for u in ranked if u["valid_invites"] >= MIN_VALID_INVITES]
    winners = qualified[:3]
    if not winners:
        return f"Campaign Winners\n\nNo qualified winners yet.\nMinimum required valid invites: {MIN_VALID_INVITES}"
    text = "Campaign Winners\n\n"
    for index, user in enumerate(winners, start=1):
        name = f"@{user['username']}" if user.get("username") else user.get("first_name") or str(user["user_id"])
        text += f"{index}. {name} - {user['valid_invites']} valid invites - Prize: {PRIZES[index]}\n"
    text += "\nOnly invited users still subscribed to the channel are counted."
    return text

@dp.message(CommandStart())
async def start(message: Message):
    inviter_id = parse_inviter(message)
    save_user(message, inviter_id)
    invite_link = f"https://t.me/{BOT_USERNAME}?start={message.from_user.id}"
    raw_invites = get_raw_invites(message.from_user.id)
    remaining = max(0, MIN_VALID_INVITES - raw_invites)
    joined = await is_channel_member(message.from_user.id)
    join_status = "Joined" if joined else "Not joined yet"
    status = "Active" if campaign_active() else "Not active yet"
    text = f"""
<b>Welcome to VoltIX VTX Referral Campaign</b>

Channel: {CHANNEL_USERNAME}
Your channel status: <b>{join_status}</b>
Campaign status: <b>{status}</b>

<b>Rewards</b>
1st place: {PRIZES[1]}
2nd place: {PRIZES[2]}
3rd place: {PRIZES[3]}

Campaign duration: <b>{CAMPAIGN_DAYS} days</b>
Minimum to qualify: <b>{MIN_VALID_INVITES}</b> valid invites

<b>Your invite link:</b>
<code>{invite_link}</code>

Recorded invites: <b>{raw_invites}</b>
Remaining to qualify: <b>{remaining}</b>

Important: An invite is valid only if the invited person stays subscribed until the campaign ends.
"""
    await message.answer(text, reply_markup=user_keyboard(invite_link))

@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer("/start - Get your invite link\n/myinvites - Show stats\n/top - Leaderboard\n/help - Help")

@dp.message(Command("myinvites"))
async def my_invites(message: Message):
    raw_invites = get_raw_invites(message.from_user.id)
    remaining = max(0, MIN_VALID_INVITES - raw_invites)
    await message.answer(f"Your Referral Stats\n\nRecorded invites: {raw_invites}\nRemaining to qualify: {remaining}")

@dp.message(Command("top"))
async def top_cmd(message: Message):
    ranked = await calculate_valid_invites()
    if not ranked:
        await message.answer("No valid referrals yet.")
        return
    text = "Live Leaderboard\n\n"
    for index, user in enumerate(ranked[:10], start=1):
        name = f"@{user['username']}" if user.get("username") else user.get("first_name") or str(user["user_id"])
        text += f"{index}. {name} - {user['valid_invites']} valid invites\n"
    await message.answer(text)

@dp.message(Command("admin"))
async def admin_cmd(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("You are not allowed to use this command.")
        return
    start_date, end_date = campaign_dates()
    status = "Active" if campaign_active() else "Inactive"
    await message.answer(f"""
<b>Admin Panel</b>

Bot: @{BOT_USERNAME}
Channel: {CHANNEL_USERNAME}
Admin ID: {message.from_user.id}

Campaign status: <b>{status}</b>
Campaign start: <code>{start_date or 'Not set'}</code>
Campaign end: <code>{end_date or 'Not set'}</code>
Users: <b>{get_total_users()}</b>
Minimum valid invites: <b>{MIN_VALID_INVITES}</b>
Duration: <b>{CAMPAIGN_DAYS} days</b>

Commands:
/start_campaign
/end_campaign
/winners
/stats
/broadcast your message
/ban user_id
/reset_campaign
""", reply_markup=admin_keyboard())

@dp.message(Command("start_campaign"))
async def start_campaign_cmd(message: Message):
    if not is_admin(message.from_user.id):
        return
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=CAMPAIGN_DAYS)
    set_setting("campaign_active", "1")
    set_setting("campaign_start", now.isoformat())
    set_setting("campaign_end", end.isoformat())
    await message.answer(f"Campaign started. End: <code>{end.isoformat()}</code>")

@dp.message(Command("end_campaign"))
async def end_campaign_cmd(message: Message):
    if not is_admin(message.from_user.id):
        return
    set_setting("campaign_active", "0")
    await message.answer("Campaign ended.\n\n" + await winners_text())

@dp.message(Command("winners"))
async def winners_cmd(message: Message):
    if is_admin(message.from_user.id):
        await message.answer(await winners_text())

@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    if not is_admin(message.from_user.id):
        return
    ranked = await calculate_valid_invites()
    total_valid = sum(u["valid_invites"] for u in ranked)
    qualified = len([u for u in ranked if u["valid_invites"] >= MIN_VALID_INVITES])
    await message.answer(f"Bot Statistics\n\nTotal users: {get_total_users()}\nCurrent valid referrals: {total_valid}\nQualified users: {qualified}")

@dp.message(Command("broadcast"))
async def broadcast_cmd(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /broadcast your message")
        return
    sent = 0
    failed = 0
    for (user_id,) in get_users():
        try:
            await bot.send_message(user_id, parts[1])
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    await message.answer(f"Broadcast completed. Sent: {sent}. Failed: {failed}")

@dp.message(Command("ban"))
async def ban_cmd(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /ban user_id")
        return
    try:
        user_id = int(parts[1])
        ban_user(user_id)
        await message.answer(f"User {user_id} has been banned.")
    except ValueError:
        await message.answer("Invalid user ID.")

@dp.message(Command("reset_campaign"))
async def reset_cmd(message: Message):
    if is_admin(message.from_user.id):
        reset_campaign()
        await message.answer("Campaign data has been reset.")

@dp.callback_query(F.data == "admin_start")
async def cb_start(callback):
    if not is_admin(callback.from_user.id):
        await callback.answer("Not allowed", show_alert=True)
        return
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=CAMPAIGN_DAYS)
    set_setting("campaign_active", "1")
    set_setting("campaign_start", now.isoformat())
    set_setting("campaign_end", end.isoformat())
    await callback.message.answer(f"Campaign started. End: <code>{end.isoformat()}</code>")
    await callback.answer()

@dp.callback_query(F.data == "admin_end")
async def cb_end(callback):
    if not is_admin(callback.from_user.id):
        await callback.answer("Not allowed", show_alert=True)
        return
    set_setting("campaign_active", "0")
    await callback.message.answer("Campaign ended.\n\n" + await winners_text())
    await callback.answer()

@dp.callback_query(F.data == "admin_winners")
async def cb_winners(callback):
    if is_admin(callback.from_user.id):
        await callback.message.answer(await winners_text())
    await callback.answer()

@dp.callback_query(F.data == "admin_stats")
async def cb_stats(callback):
    if not is_admin(callback.from_user.id):
        await callback.answer("Not allowed", show_alert=True)
        return
    ranked = await calculate_valid_invites()
    total_valid = sum(u["valid_invites"] for u in ranked)
    await callback.message.answer(f"Stats\n\nUsers: {get_total_users()}\nValid referrals: {total_valid}")
    await callback.answer()

async def health(request):
    return web.Response(text="VoltIX bot is running.")

async def run_web_server():
    app = web.Application()
    app.router.add_get("/", health)
    port = int(os.getenv("PORT", "10000"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info("Health server started on port %s", port)

async def main():
    if BOT_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
        raise RuntimeError("BOT_TOKEN is missing. Add it in Render Environment Variables.")
    init_db()
    await run_web_server()
    logging.info("Bot polling started.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

import os
import sqlite3
import random
import asyncio
from datetime import datetime, timedelta, timezone

from flask import Flask
from threading import Thread

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ANNOUNCE_CHANNEL = os.getenv("ANNOUNCE_CHANNEL")

DB = "lottery.db"

app = Flask(__name__)


@app.route("/")
def home():
    return "Lottery Bot is running."


def keep_alive():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = db()
    con.execute("""
        CREATE TABLE IF NOT EXISTS lotteries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            winners INTEGER NOT NULL,
            ends_at TEXT NOT NULL,
            channels TEXT,
            description TEXT,
            message_id INTEGER,
            status TEXT DEFAULT 'active'
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS participants (
            lottery_id INTEGER,
            user_id INTEGER,
            username TEXT,
            first_name TEXT,
            joined_at TEXT,
            UNIQUE(lottery_id, user_id)
        )
    """)

    con.commit()
    con.close()


def is_admin(user_id):
    return user_id == ADMIN_ID


def lottery_text(lottery_id):
    con = db()
    l = con.execute(
        "SELECT * FROM lotteries WHERE id=?",
        (lottery_id,)
    ).fetchone()

    count = con.execute(
        "SELECT COUNT(*) FROM participants WHERE lottery_id=?",
        (lottery_id,)
    ).fetchone()[0]

    con.close()

    if not l:
        return None

    end = datetime.fromisoformat(l["ends_at"])
    now = datetime.now(timezone.utc)

    remaining = end - now

    if remaining.total_seconds() <= 0:
        timer = "⛔ پایان یافته"
    else:
        total = int(remaining.total_seconds())
        days, rem = divmod(total, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, seconds = divmod(rem, 60)

        if days:
            timer = f"{days}روز {hours:02d}:{minutes:02d}:{seconds:02d}"
        else:
            timer = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    text = f"""
🎁 <b>{l['title']}</b>

🏆 تعداد برنده: <b>{l['winners']}</b>
👥 شرکت‌کنندگان: <b>{count}</b>

⏳ زمان باقی‌مانده:
<b>{timer}</b>
"""

    if l["description"]:
        text += f"\n📝 {l['description']}\n"

    text += "\nبرای شرکت در قرعه‌کشی روی دکمه زیر بزنید."

    return text


def lottery_keyboard(lottery_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎟 شرکت در قرعه‌کشی",
                callback_data=f"join:{lottery_id}"
            )
        ]
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args

    if not args:
        await update.message.reply_text(
            "🎰 به ربات قرعه‌کشی خوش آمدید."
        )
        return

    if not args[0].startswith("lottery_"):
        return

    try:
        lottery_id = int(args[0].split("_")[1])
    except:
        return

    con = db()
    lottery = con.execute(
        "SELECT * FROM lotteries WHERE id=?",
        (lottery_id,)
    ).fetchone()
    con.close()

    if not lottery:
        await update.message.reply_text("❌ این قرعه‌کشی وجود ندارد.")
        return

    await update.message.reply_text(
        lottery_text(lottery_id),
        parse_mode="HTML",
        reply_markup=lottery_keyboard(lottery_id)
    )


async def join_lottery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    lottery_id = int(query.data.split(":")[1])
    user = query.from_user

    con = db()
    lottery = con.execute(
        "SELECT * FROM lotteries WHERE id=?",
        (lottery_id,)
    ).fetchone()
    con.close()

    if not lottery:
        await query.message.reply_text("❌ قرعه‌کشی پیدا نشد.")
        return

    end = datetime.fromisoformat(lottery["ends_at"])

    if datetime.now(timezone.utc) >= end:
        await query.message.reply_text("⛔ زمان این قرعه‌کشی تمام شده.")
        return

    channels = [
        x.strip()
        for x in (lottery["channels"] or "").split(",")
        if x.strip()
    ]

    buttons = []

    for channel in channels:
        clean = channel.replace("@", "")

                        try:
            member = await context.bot.get_chat_member(
                f"@{clean}",
                user.id
            )

            if member.status in ["left", "kicked"]:
                buttons.append([
                    InlineKeyboardButton(
                        f"📢 عضویت در {channel}",
                        url=f"https://t.me/{clean}"
                    )
                ])

        except Exception:
            buttons.append([
                InlineKeyboardButton(
                    f"📢 عضویت در {channel}",
                    url=f"https://t.me/{clean}"
                )
            ])

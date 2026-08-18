import os
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from threading import Thread

from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL = os.getenv("ANNOUNCE_CHANNEL", "")
DB = "lottery.db"

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is running"


def web():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def setup():
    c = db()
    c.execute("""
        CREATE TABLE IF NOT EXISTS lottery (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            winners INTEGER,
            end TEXT,
            channels TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            lottery_id INTEGER,
            user_id INTEGER,
            username TEXT,
            UNIQUE(lottery_id, user_id)
        )
    """)
    c.commit()
    c.close()


def admin(uid):
    return uid == ADMIN_ID


def left_time(end):
    s = int(
        (
            datetime.fromisoformat(end)
            - datetime.now(timezone.utc)
        ).total_seconds()
    )

    if s <= 0:
        return "⛔ تمام شده"

    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def info(lottery):
    c = db()
    count = c.execute(
        "SELECT COUNT(*) FROM users WHERE lottery_id=?",
        (lottery["id"],)
    ).fetchone()[0]
    c.close()

    return (
        f"🎁 <b>{lottery['title']}</b>\n\n"
        f"🏆 تعداد برنده: {lottery['winners']}\n"
        f"👥 شرکت‌کنندگان: {count}\n"
        f"⏱ زمان باقی‌مانده: {left_time(lottery['end'])}"
    )


def join_button(lid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🎰 شرکت در قرعه‌کشی",
            callback_data=f"join:{lid}"
        )]
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    if not context.args:
        await update.message.reply_text(
            "🎰 ربات قرعه‌کشی آماده است."
        )
        return

    try:
        lid = int(context.args[0].replace("lottery_", ""))
    except:
        return

    c = db()
    lottery = c.execute(
        "SELECT * FROM lottery WHERE id=?",
        (lid,)
    ).fetchone()
    c.close()

    if not lottery:
        await update.message.reply_text(
            "❌ قرعه‌کشی پیدا نشد."
        )
        return

    await update.message.reply_text(
        info(lottery),
        parse_mode="HTML",
        reply_markup=join_button(lid)
    )


async def join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    lid = int(q.data.split(":")[1])

    c = db()
    lottery = c.execute(
        "SELECT * FROM lottery WHERE id=?",
        (lid,)
    ).fetchone()
    c.close()

    if not lottery:
        await q.message.reply_text("❌ قرعه‌کشی پیدا نشد.")
        return

    if datetime.now(timezone.utc) >= datetime.fromisoformat(lottery["end"]):
        await q.message.reply_text("⛔ زمان تمام شده.")
        return

    missing = []

    for channel in (lottery["channels"] or "").split(","):
        channel = channel.strip()

        if not channel:
            continue

        clean = channel.replace("@", "")

        try:
            member = await context.bot.get_chat_member(
                f"@{clean}",
                q.from_user.id
            )

            if member.status in ("left", "kicked"):
                missing.append(clean)

        except:
            missing.append(clean)

    if missing:
        buttons = []

        for channel in missing:
            buttons.append([
                InlineKeyboardButton(
                    f"📢 عضویت @{channel}",
                    url=f"https://t.me/{channel}"
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                "✅ بررسی عضویت",
                callback_data=f"join:{lid}"
            )
        ])

        await q.message.reply_text(
            "❌ ابتدا عضو کانال‌های زیر شوید:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    c = db()

    try:
        c.execute(
            """
            INSERT INTO users
            (lottery_id,user_id,username)
            VALUES (?,?,?)
            """,
            (
                lid,
                q.from_user.id,
                q.from_user.username or ""
            )
        )

        c.commit()

        await q.message.reply_text(
            "🎉 با موفقیت ثبت شد!"
        )

    except sqlite3.IntegrityError:
        await q.message.reply_text(
            "ℹ️ شما قبلاً شرکت کرده‌اید."
        )

    finally:
        c.close()


async def new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin(update.effective_user.id):
        return

    context.user_data.clear()
    context.user_data["step"] = 1

    await update.message.reply_text(
        "🎁 عنوان قرعه‌کشی را بفرست:\n"
        "مثال: 1,000,000 میو"
    )


async def admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin(update.effective_user.id):
        return

    step = context.user_data.get("step")

    if not step:
        return

    value = update.message.text.strip()

    if step == 1:
        context.user_data["title"] = value
        context.user_data["step"] = 2

        await update.message.reply_text(
            "🏆 چند برنده؟"
        )

    elif step == 2:
        try:
            winners = int(value)
            if winners < 1:
                raise ValueError
        except:
            await update.message.reply_text(
                "❌ یک عدد معتبر بفرست."
            )
            return

        context.user_data["winners"] = winners
        context.user_data["step"] = 3

        await update.message.reply_text(
            "⏱ چند ساعت تا پایان؟"
        )

    elif step == 3:
        try:
            hours = float(value)
            if hours <= 0:
                raise ValueError
        except:
            await update.message.reply_text(
                "❌ تعداد ساعت نامعتبر است."
            )
            return

        context.user_data["hours"] = hours
        context.user_data["step"] = 4

        await update.message.reply_text(
            "📢 کانال عضویت اجباری را بفرست.\n"
            "مثال: @channel\n"
            "یا بنویس: none"
        )

    elif step == 4:
        channels = "" if value.lower() == "none" else value

        end = (
            datetime.now(timezone.utc)
            + timedelta(hours=context.user_data["hours"])
        ).isoformat()

        c = db()

        cur = c.execute(
            """
            INSERT INTO lottery
            (title,winners,end,channels)
            VALUES (?,?,?,?)
            """,
            (
                context.user_data["title"],
                context.user_data["winners"],
                end,
                channels
            )
        )

        lid = cur.lastrowid
        c.commit()

        lottery = c.execute(
            "SELECT * FROM lottery WHERE id=?",
            (lid,)
        ).fetchone()

        c.close()

        link = (
            f"https://t.me/{context.bot.username}"
            f"?start=lottery_{lid}"
        )

        msg = info(lottery) + f"\n\n🔗 لینک شرکت:\n{link}"

        try:
            await context.bot.send_message(
                chat_id=CHANNEL,
                text=msg,
                parse_mode="HTML",
                reply_markup=join_button(lid)
            )

            await update.message.reply_text(
                "✅ قرعه‌کشی ساخته شد!\n\n" + msg
            )

        except Exception as e:
            await update.message.reply_text(
                f"❌ ارسال به کانال ناموفق بود:\n{e}"
            )

        context.user_data.clear()


async def draw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text(
            "مثال:\n/draw 1"
        )
        return

    try:
        lid = int(context.args[0])
    except:
        await update.message.reply_text("❌ شماره نامعتبر است.")
        return

    c = db()

    lottery = c.execute(
        "SELECT * FROM lottery WHERE id=?",
        (lid,)
    ).fetchone()

    users = c.execute(
        "SELECT * FROM users WHERE lottery_id=?",
        (lid,)
    ).fetchall()

    c.close()

    if not lottery:
        await update.message.reply_text(
            "❌ قرعه‌کشی پیدا نشد."
        )
        return

    if not users:
        await update.message.reply_text(
            "❌ کسی در قرعه‌کشی شرکت نکرده."
        )
        return

    count = min(lottery["winners"], len(users))
    winners = random.sample(users, count)

    result = "🏆 <b>نتیجه قرعه‌کشی</b>\n\n"

    for i, user in enumerate(winners, 1):
        name = (
            f"@{user['username']}"
            if user["username"]
            else str(user["user_id"])
        )

        result += f"{i}. {name}\n"

    await update.message.reply_text(
        result,
        parse_mode="HTML"
    )


def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN تنظیم نشده است.")

    setup()

    Thread(target=web, daemon=True).start()

    bot = Application.builder().token(TOKEN).build()

    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("new", new))
    bot.add_handler(CommandHandler("draw", draw))

    bot.add_handler(
        CallbackQueryHandler(
            join,
            pattern=r"^join:"
        )
    )

    bot.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            admin_text
        )
    )

    print("BOT STARTED")

    bot.run_polling()


if __name__ == "__main__":
    main()

import os
import sqlite3
import random
from datetime import datetime, timedelta, timezone
from threading import Thread

from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL = os.getenv("ANNOUNCE_CHANNEL", "")
DB = "lottery.db"

app = Flask(__name__)


@app.route("/")
def home():
    return "Lottery Bot is running."


def keep_alive():
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "10000"))
    )


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = db()

    con.execute("""
        CREATE TABLE IF NOT EXISTS lotteries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            winners INTEGER,
            ends_at TEXT,
            channels TEXT,
            message_id INTEGER,
            status TEXT DEFAULT 'active'
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lottery_id INTEGER,
            user_id INTEGER,
            username TEXT,
            name TEXT,
            UNIQUE(lottery_id, user_id)
        )
    """)

    con.commit()
    con.close()


def is_admin(user_id):
    return user_id == ADMIN_ID


def remaining(end):
    end = datetime.fromisoformat(end)
    sec = int((end - datetime.now(timezone.utc)).total_seconds())

    if sec <= 0:
        return "⛔ تمام شده"

    d, sec = divmod(sec, 86400)
    h, sec = divmod(sec, 3600)
    m, s = divmod(sec, 60)

    if d:
        return f"{d} روز {h:02d}:{m:02d}:{s:02d}"

    return f"{h:02d}:{m:02d}:{s:02d}"


def lottery_info(lottery):
    con = db()

    count = con.execute(
        "SELECT COUNT(*) FROM participants WHERE lottery_id=?",
        (lottery["id"],)
    ).fetchone()[0]

    con.close()

    return (
        f"🎁 <b>{lottery['title']}</b>\n\n"
        f"🏆 برنده: {lottery['winners']} نفر\n"
        f"👥 شرکت‌کنندگان: {count}\n"
        f"⏱ زمان باقی‌مانده: {remaining(lottery['ends_at'])}\n\n"
        f"برای شرکت روی دکمه زیر بزنید."
    )


def join_button(lottery_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎰 شرکت در قرعه‌کشی",
                callback_data=f"join:{lottery_id}"
            )
        ]
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    if not context.args:
        await update.message.reply_text(
            "🎰 به ربات قرعه‌کشی خوش آمدید."
        )
        return

    arg = context.args[0]

    if not arg.startswith("lottery_"):
        return

    try:
        lottery_id = int(arg.split("_")[1])
    except:
        await update.message.reply_text("❌ لینک نامعتبر است.")
        return

    con = db()
    lottery = con.execute(
        "SELECT * FROM lotteries WHERE id=?",
        (lottery_id,)
    ).fetchone()
    con.close()
if not lottery:
        await update.message.reply_text(
            "❌ قرعه‌کشی پیدا نشد."
        )
        return

    await update.message.reply_text(
        lottery_info(lottery),
        parse_mode="HTML",
        reply_markup=join_button(lottery_id)
    )


async def join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    lottery_id = int(query.data.split(":")[1])

    con = db()
    lottery = con.execute(
        "SELECT * FROM lotteries WHERE id=?",
        (lottery_id,)
    ).fetchone()
    con.close()

    if not lottery:
        await query.message.reply_text(
            "❌ قرعه‌کشی پیدا نشد."
        )
        return

    if datetime.now(timezone.utc) >= datetime.fromisoformat(
        lottery["ends_at"]
    ):
        await query.message.reply_text(
            "⛔ زمان قرعه‌کشی تمام شده."
        )
        return

    user = query.from_user
    missing = []

    channels = [
        x.strip()
        for x in (lottery["channels"] or "").split(",")
        if x.strip()
    ]

    for channel in channels:
        clean = channel.replace("@", "")

        try:
            member = await context.bot.get_chat_member(
                f"@{clean}",
                user.id
            )

            if member.status in ("left", "kicked"):
                missing.append(clean)

        except Exception:
            missing.append(clean)

    if missing:
        buttons = []

        for channel in missing:
            buttons.append([
                InlineKeyboardButton(
                    f"📢 عضویت در @{channel}",
                    url=f"https://t.me/{channel}"
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                "✅ بررسی عضویت",
                callback_data=f"join:{lottery_id}"
            )
        ])

        await query.message.reply_text(
            "❌ ابتدا در کانال‌های زیر عضو شوید:",
      reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    con = db()

    try:
        con.execute("""
            INSERT INTO participants
            (lottery_id,user_id,username,name)
            VALUES (?,?,?,?)
        """, (
            lottery_id,
            user.id,
            user.username or "",
            user.first_name or ""
        ))

        con.commit()

        await query.message.reply_text(
            "🎉 با موفقیت در قرعه‌کشی ثبت شد!"
        )

    except sqlite3.IntegrityError:
        await query.message.reply_text(
            "ℹ️ شما قبلاً شرکت کرده‌اید."
        )

    finally:
        con.close()


async def new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    context.user_data.clear()
    context.user_data["step"] = "title"

    await update.message.reply_text(
        "🎁 عنوان قرعه‌کشی را بفرست.\n"
        "مثال: 1,000,000 میو"
    )


async def admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    if not is_admin(update.effective_user.id):
        return

    step = context.user_data.get("step")

    if not step:
        return

    text = update.message.text.strip()

    if step == "title":
        context.user_data["title"] = text
        context.user_data["step"] = "winners"

        await update.message.reply_text(
            "🏆 تعداد برنده‌ها؟"
        )

    elif step == "winners":
        try:
            winners = int(text)
            if winners < 1:
                raise ValueError
        except:
            await update.message.reply_text(
                "❌ یک عدد معتبر بفرست."
            )
            return

        context.user_data["winners"] = winners
        context.user_data["step"] = "hours"

        await update.message.reply_text(
            "⏱ چند ساعت تا پایان؟"
        )

    elif step == "hours":
        try:
            hours = float(text)
            if hours <= 0:
                raise ValueError
        except:
            await update.message.reply_text(
                "❌ تعداد ساعت نامعتبر است."
            )
            return

        context.user_data["hours"] = hours
        context.user_data["step"] = "channels"

        await update.message.reply_text(
            "📢 کانال‌های عضویت اجباری را بفرست.\n"
            "مثال: @channel1,@channel2\n\n"
            "اگر نمی‌خواهی: none"
        )

    elif step == "channels":
        channels = "" if text.lower() == "none" else text

        title = context.user_data["title"]
        winners = context.user_data["winners"]
        hours = context.user_data["hours"]

        end = (
            datetime.now(timezone.utc)
            + timedelta(hours=hours)
        )

        con = db()

        cur = con.execute("""
            INSERT INTO lotteries
            (title,winners,ends_at,channels)
            VALUES (?,?,?,?)
        """, (
            title,
            winners,
            end.isoformat(),
            channels
        ))

        lottery_id = cur.lastrowid
        con.commit()
        con.close()

        link = (
            f"https://t.me/{context.bot.username}"
            f"?start=lottery_{lottery_id}"
        )

        con = db()
        lottery = con.execute(
            "SELECT * FROM lotteries WHERE id=?",
            (lottery_id,)
        ).fetchone()
        con.close()

        message = (
            lottery_info(lottery)
            + f"\n\n🔗 لینک شرکت:\n{link}"
        )

        try:
            sent = await context.bot.send_message(
                chat_id=CHANNEL,
                text=message,
                parse_mode="HTML",
                reply_markup=join_button(lottery_id)
            )

            con = db()
            con.execute(
                "UPDATE lotteries SET message_id=? WHERE id=?",
                (sent.message_id, lottery_id)
            )
            con.commit()
            con.close()

        except Exception as e:
            await update.message.reply_text(
                f"❌ ارسال به کانال ناموفق بود:\n{e}"
            )
            context.user_data.clear()
            return

        await update.message.reply_text(
            "✅ قرعه‌کشی ساخته شد!\n\n"
            f"🎁 {title}\n"
            f"🏆 {winners} برنده\n"
            f"⏱ {hours} ساعت\n\n"
            f"🔗 {link}"
        )

        context.user_data.clear()


async def draw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text(
            "مثال:\n/draw 1"
        )
        return

    try:
        lottery_id = int(context.args[0])
    except:
        await update.message.reply_text(
            "❌ شماره نامعتبر است."
        )
        return

    con = db()

    lottery = con.execute(
        "SELECT * FROM lotteries WHERE id=?",
        (lottery_id,)
    ).fetchone()

    users = con.execute(
        "SELECT * FROM participants WHERE lottery_id=?",
        (lottery_id,)
    ).fetchall()

    if not lottery:
        con.close()
        await update.message.reply_text(
            "❌ قرعه‌کشی پیدا نشد."
        )  
return

    if not users:
        con.close()
        await update.message.reply_text(
            "❌ هنوز کسی شرکت نکرده."
        )
        return

    count = min(lottery["winners"], len(users))
    winners = random.sample(users, count)

    con.execute(
        "UPDATE lotteries SET status='drawn' WHERE id=?",
        (lottery_id,)
    )

    con.commit()
    con.close()

    result = "🏆 <b>نتیجه قرعه‌کشی</b>\n\n"

    for i, winner in enumerate(winners, 1):
        name = (
            f"@{winner['username']}"
            if winner["username"]
            else winner["name"]
        )

        result += f"{i}. {name}\n"

    await update.message.reply_text(
        result,
        parse_mode="HTML"
    )


async def timer(context: ContextTypes.DEFAULT_TYPE):
    con = db()

    lotteries = con.execute(
        "SELECT * FROM lotteries WHERE status='active'"
    ).fetchall()

    for lottery in lotteries:

        if datetime.now(timezone.utc) >= datetime.fromisoformat(
            lottery["ends_at"]
        ):
            con.execute(
                "UPDATE lotteries SET status='ended' WHERE id=?",
                (lottery["id"],)
            )
            continue

        if lottery["message_id"]:

            try:
                link = (
                    f"https://t.me/{context.bot.username}"
                    f"?start=lottery_{lottery['id']}"
                )

                text = (
                    lottery_info(lottery)
                    + f"\n\n🔗 لینک شرکت:\n{link}"
                )

                await context.bot.edit_message_text(
                    chat_id=CHANNEL,
                    message_id=lottery["message_id"],
                    text=text,
                    parse_mode="HTML",
                    reply_markup=join_button(lottery["id"])
                )

            except:
                pass

    con.commit()
    con.close()


def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")

    init_db()

    Thread(
        target=keep_alive,
        daemon=True
    ).start()

    application = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("new", new)
    )

    application.add_handler(
        CommandHandler("draw", draw)
    )

    application.add_handler(
        CallbackQueryHandler(
            join,
            pattern=r"^join:"
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            admin_text
        )
    )

    application.job_queue.run_repeating(
        timer,
        interval=60,
        first=10
    )

    print("BOT STARTED")

    application.run_polling()


if __name__ == "__main__":
    main()

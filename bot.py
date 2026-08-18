import os
import random
import sqlite3
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

# =========================
# CONFIG
# =========================

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL = os.getenv("ANNOUNCE_CHANNEL", "")

DB_FILE = "lottery.db"

app = Flask(__name__)


# =========================
# RENDER WEB SERVER
# =========================

@app.route("/")
def home():
    return "Lottery Bot is running."


def run_web():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)


# =========================
# DATABASE
# =========================

def get_db():
    db = sqlite3.connect(DB_FILE)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    db = get_db()

    db.execute("""
        CREATE TABLE IF NOT EXISTS lotteries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            winners INTEGER NOT NULL,
            end_time TEXT NOT NULL,
            channels TEXT DEFAULT '',
            status TEXT DEFAULT 'active'
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS participants (
            lottery_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            UNIQUE(lottery_id, user_id)
        )
    """)

    db.commit()
    db.close()


# =========================
# HELPERS
# =========================

def is_admin(user_id):
    return user_id == ADMIN_ID


def get_lottery(lottery_id):
    db = get_db()

    lottery = db.execute(
        "SELECT * FROM lotteries WHERE id=?",
        (lottery_id,)
    ).fetchone()

    db.close()

    return lottery


def participant_count(lottery_id):
    db = get_db()

    count = db.execute(
        "SELECT COUNT(*) FROM participants WHERE lottery_id=?",
        (lottery_id,)
    ).fetchone()[0]

    db.close()

    return count


def time_left(end_time):
    end = datetime.fromisoformat(end_time)

    seconds = int(
        (end - datetime.now(timezone.utc)).total_seconds()
    )

    if seconds <= 0:
        return "⛔ تمام شده"

    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    if days:
        return f"{days} روز، {hours:02d}:{minutes:02d}:{seconds:02d}"

    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def lottery_text(lottery):
    count = participant_count(lottery["id"])

    return (
        f"🎁 <b>{lottery['title']}</b>\n\n"
        f"🏆 تعداد برنده: {lottery['winners']} نفر\n"
        f"👥 شرکت‌کنندگان: {count}\n"
        f"⏱ زمان باقی‌مانده: {time_left(lottery['end_time'])}\n\n"
        f"برای شرکت روی دکمه زیر بزنید."
    )


def lottery_keyboard(lottery_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎰 شرکت در قرعه‌کشی",
                callback_data=f"join:{lottery_id}"
            )
        ]
    ])


def bot_link(username, lottery_id):
    return (
        f"https://t.me/{username}"
        f"?start=lottery_{lottery_id}"
    )


# =========================
# START
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message:
        return

    if not context.args:
        await update.message.reply_text(
            "🎰 به ربات قرعه‌کشی خوش آمدید."
        )
        return

    argument = context.args[0]

    if not argument.startswith("lottery_"):
        return

    try:
        lottery_id = int(
            argument.replace("lottery_", "")
        )
    except ValueError:
        await update.message.reply_text(
            "❌ لینک قرعه‌کشی نامعتبر است."
        )
        return

    lottery = get_lottery(lottery_id)

    if not lottery:
        await update.message.reply_text(
            "❌ این قرعه‌کشی وجود ندارد."
        )
        return

    await update.message.reply_text(
        lottery_text(lottery),
        parse_mode="HTML",
        reply_markup=lottery_keyboard(lottery_id)
    )


# =========================
# JOIN LOTTERY
# =========================

async def join_lottery(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    lottery_id = int(
        query.data.split(":")[1]
    )

    lottery = get_lottery(lottery_id)

    if not lottery:
        await query.message.reply_text(
            "❌ قرعه‌کشی پیدا نشد."
        )
        return

    if lottery["status"] != "active":
        await query.message.reply_text(
            "⛔ این قرعه‌کشی فعال نیست."
        )
        return

    if datetime.now(timezone.utc) >= datetime.fromisoformat(
        lottery["end_time"]
    ):
        await query.message.reply_text(
            "⛔ زمان قرعه‌کشی تمام شده."
        )
        return

    # =====================
    # REQUIRED CHANNELS
    # =====================

    missing_channels = []

    channels = lottery["channels"] or ""

    for channel in channels.split(","):

        channel = channel.strip()

        if not channel:
            continue

        clean = channel.replace("@", "")

        try:

            member = await context.bot.get_chat_member(
                f"@{clean}",
                query.from_user.id
            )

            if member.status in (
                "left",
                "kicked"
            ):
                missing_channels.append(clean)

        except Exception:
            missing_channels.append(clean)

    # =====================
    # NOT MEMBER
    # =====================

    if missing_channels:

        buttons = []

        for channel in missing_channels:

            buttons.append([
                InlineKeyboardButton(
                    f"📢 عضویت @{channel}",
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
            "❌ برای شرکت ابتدا در کانال‌های زیر عضو شوید:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

        return

    # =====================
    # SAVE USER
    # =====================

    db = get_db()

    try:

        db.execute(
            """
            INSERT INTO participants
            (
                lottery_id,
                user_id,
                username,
                first_name
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                lottery_id,
                query.from_user.id,
                query.from_user.username or "",
                query.from_user.first_name or ""
            )
        )

        db.commit()

        await query.message.reply_text(
            "🎉 با موفقیت در قرعه‌کشی ثبت شد!"
        )

    except sqlite3.IntegrityError:

        await query.message.reply_text(
            "ℹ️ شما قبلاً در این قرعه‌کشی شرکت کرده‌اید."
        )

    finally:
        db.close()


# =========================
# CREATE LOTTERY
# =========================

async def new_lottery(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(update.effective_user.id):
        return

    context.user_data.clear()

    context.user_data["step"] = "title"

    await update.message.reply_text(
        "🎁 عنوان جایزه را بفرست.\n\n"
        "مثال:\n"
        "1,000,000 میو"
    )


# =========================
# ADMIN INPUT
# =========================

async def admin_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    if not is_admin(update.effective_user.id):
        return

    step = context.user_data.get("step")

    if not step:
        return

    value = update.message.text.strip()

    # =====================
    # TITLE
    # =====================

    if step == "title":

        context.user_data["title"] = value
        context.user_data["step"] = "winners"

        await update.message.reply_text(
            "🏆 چند برنده می‌خواهی؟\n\n"
            "مثال: 1"
        )

        return

    # =====================
    # WINNERS
    # =====================

    if step == "winners":

        try:

            winners = int(value)

            if winners < 1:
                raise ValueError

        except ValueError:

            await update.message.reply_text(
                "❌ لطفاً یک عدد معتبر وارد کن."
            )

            return

        context.user_data["winners"] = winners
        context.user_data["step"] = "duration"

        await update.message.reply_text(
            "⏱ قرعه‌کشی چند ساعت فعال باشد؟\n\n"
            "مثال: 24"
        )

        return

    # =====================
    # DURATION
    # =====================

    if step == "duration":

        try:

            hours = float(value)

            if hours <= 0:
                raise ValueError

        except ValueError:

            await update.message.reply_text(
                "❌ تعداد ساعت نامعتبر است."
            )

            return

        context.user_data["hours"] = hours
        context.user_data["step"] = "channels"

        await update.message.reply_text(
            "📢 کانال عضویت اجباری را بفرست.\n\n"
            "مثال:\n"
            "@channel\n\n"
            "اگر عضویت اجباری نمی‌خواهی:\n"
            "none"
        )

        return

    # =====================
    # CHANNELS
    # =====================

    if step == "channels":

        channels = ""

        if value.lower() != "none":
            channels = value

        title = context.user_data["title"]
        winners = context.user_data["winners"]
        hours = context.user_data["hours"]

        end_time = (
            datetime.now(timezone.utc)
            + timedelta(hours=hours)
        ).isoformat()

        db = get_db()

        cursor = db.execute(
            """
            INSERT INTO lotteries
            (
                title,
                winners,
                end_time,
                channels
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                title,
                winners,
                end_time,
                channels
            )
        )

        lottery_id = cursor.lastrowid

        db.commit()
        db.close()

        lottery = get_lottery(lottery_id)

        username = context.bot.username

        link = bot_link(
            username,
            lottery_id
        )

        message = (
            lottery_text(lottery)
            + "\n\n"
            f"🔗 لینک شرکت:\n{link}"
        )

        try:

            await context.bot.send_message(
                chat_id=CHANNEL,
                text=message,
                parse_mode="HTML",
                reply_markup=lottery_keyboard(lottery_id)
            )

            await update.message.reply_text(
                "✅ قرعه‌کشی ساخته شد!\n\n"
                + message
            )

        except Exception as error:

            await update.message.reply_text(
                "❌ ارسال به کانال ناموفق بود.\n\n"
                f"{error}"
            )

        context.user_data.clear()


# =========================
# DRAW
# =========================

async def draw(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(update.effective_user.id):
        return

    if not context.args:

        await update.message.reply_text(
            "مثال:\n"
            "/draw 1"
        )

        return

    try:

        lottery_id = int(
            context.args[0]
        )

    except ValueError:

        await update.message.reply_text(
            "❌ شناسه قرعه‌کشی نامعتبر است."
        )

        return

    lottery = get_lottery(lottery_id)

    if not lottery:

        await update.message.reply_text(
            "❌ قرعه‌کشی پیدا نشد."
        )

        return

    db = get_db()

    participants = db.execute(
        """
        SELECT *
        FROM participants
        WHERE lottery_id=?
        """,
        (lottery_id,)
    ).fetchall()

    db.close()

    if not participants:

        await update.message.reply_text(
            "❌ هنوز کسی شرکت نکرده."
        )

        return

    winners_count = min(
        lottery["winners"],
        len(participants)
    )

    winners = random.sample(
        participants,
        winners_count
    )

    # =====================
    # RESULT
    # =====================

    result = (
        "🎉 <b>نتیجه قرعه‌کشی</b>\n\n"
        f"🎁 جایزه: <b>{lottery['title']}</b>\n\n"
    )

    for index, winner in enumerate(
        winners,
        start=1
    ):

        name = winner["first_name"] or "بدون نام"

        username = winner["username"]

        if username:
            username_text = f"@{username}"
        else:
            username_text = "ندارد"

        result += (
            f"🏆 <b>برنده {index}</b>\n"
            f"👤 نام: {name}\n"
            f"🔗 یوزرنیم: {username_text}\n"
            f"🆔 آیدی عددی: "
            f"<code>{winner['user_id']}</code>\n\n"
        )

    # =====================
    # ADMIN RESULT
    # =====================

    await update.message.reply_text(
        result,
        parse_mode="HTML"
    )

    # =====================
    # CHANNEL RESULT
    # =====================

    try:

        await context.bot.send_message(
            chat_id=CHANNEL,
            text=(
                "🎊 <b>قرعه‌کشی به پایان رسید!</b>\n\n"
                + result
                + "❤️ ممنون از شرکت شما"
            ),
            parse_mode="HTML"
        )

    except Exception as error:

        await update.message.reply_text(
            "⚠️ نتیجه برای کانال ارسال نشد:\n"
            f"{error}"
        )


# =========================
# MAIN
# =========================

def main():

    if not TOKEN:
        raise RuntimeError(
            "BOT_TOKEN تنظیم نشده است."
        )

    if not ADMIN_ID:
        raise RuntimeError(
            "ADMIN_ID تنظیم نشده است."
        )

    if not CHANNEL:
        raise RuntimeError(
            "ANNOUNCE_CHANNEL تنظیم نشده است."
        )

    init_db()

    Thread(
        target=run_web,
        daemon=True
    ).start()

    application = (
        Application
        .builder()
        .token(TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CommandHandler(
            "new",
            new_lottery
        )
    )

    application.add_handler(
        CommandHandler(
            "draw",
            draw
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            join_lottery,
            pattern=r"^join:"
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            admin_input
        )
    )

    print("BOT STARTED")

    application.run_polling()


if __name__ == "__main__":
    main()

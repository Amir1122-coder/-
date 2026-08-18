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
    filters
)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7111630140"))
CHANNEL = os.getenv("ANNOUNCE_CHANNEL", "@meow_lottery")

DB = "lottery.db"

app = Flask(__name__)


# =========================
# WEB SERVER
# =========================

@app.route("/")
def home():
    return "Meow Lottery Bot is running."


def web_server():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)


# =========================
# DATABASE
# =========================

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def setup_database():
    con = db()

    con.execute("""
        CREATE TABLE IF NOT EXISTS lotteries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            winners INTEGER NOT NULL,
            end_time TEXT NOT NULL,
            channels TEXT DEFAULT '',
            status TEXT DEFAULT 'active'
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS participants (
            lottery_id INTEGER,
            user_id INTEGER,
            username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            UNIQUE(lottery_id, user_id)
        )
    """)

    con.commit()
    con.close()


# =========================
# HELPERS
# =========================

def is_admin(user_id):
    return user_id == ADMIN_ID


def get_lottery(lottery_id):
    con = db()

    lottery = con.execute(
        "SELECT * FROM lotteries WHERE id=?",
        (lottery_id,)
    ).fetchone()

    con.close()
    return lottery


def get_participants(lottery_id):
    con = db()

    users = con.execute(
        """
        SELECT *
        FROM participants
        WHERE lottery_id=?
        """,
        (lottery_id,)
    ).fetchall()

    con.close()
    return users


def participant_count(lottery_id):
    con = db()

    count = con.execute(
        """
        SELECT COUNT(*)
        FROM participants
        WHERE lottery_id=?
        """,
        (lottery_id,)
    ).fetchone()[0]

    con.close()
    return count


def remaining_time(end_time):
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
        return (
            f"{days} روز "
            f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        )

    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def lottery_message(lottery):
    count = participant_count(lottery["id"])

    return (
        f"🎁 <b>{lottery['title']}</b>\n\n"
        f"🏆 برنده‌ها: {lottery['winners']} نفر\n"
        f"👥 شرکت‌کنندگان: {count}\n"
        f"⏱ زمان باقی‌مانده: "
        f"{remaining_time(lottery['end_time'])}\n\n"
        "👇 برای شرکت روی دکمه زیر بزنید."
    )


def lottery_button(lottery_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎰 شرکت در قرعه‌کشی",
                callback_data=f"join:{lottery_id}"
            )
        ]
    ])


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

    arg = context.args[0]

    if not arg.startswith("lottery_"):
        return

    try:
        lottery_id = int(
            arg.replace("lottery_", "")
        )
    except ValueError:
        await update.message.reply_text(
            "❌ لینک نامعتبر است."
        )
        return

    lottery = get_lottery(lottery_id)

    if not lottery:
        await update.message.reply_text(
            "❌ قرعه‌کشی پیدا نشد."
        )
        return

    await update.message.reply_text(
        lottery_message(lottery),
        parse_mode="HTML",
        reply_markup=lottery_button(lottery_id)
    )


# =========================
# JOIN
# =========================

async def join(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    lottery_id = int(
        query.data.split(":")[1]
    )

    lottery = get_lottery(lottery_id)

    if not lottery:
        await query.answer(
            "❌ قرعه‌کشی پیدا نشد.",
            show_alert=True
        )
        return

    if lottery["status"] != "active":
        await query.answer(
            "⛔ این قرعه‌کشی تمام شده.",
            show_alert=True
        )
        return

    if datetime.now(timezone.utc) >= datetime.fromisoformat(
        lottery["end_time"]
    ):
        await query.answer(
            "⛔ زمان قرعه‌کشی تمام شده.",
            show_alert=True
        )
        return

    missing = []

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

            if member.status in ("left", "kicked"):
                missing.append(clean)

        except Exception:
            missing.append(clean)

    # کاربر عضو کانال نیست
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
                "🔄 بررسی عضویت",
                callback_data=f"join:{lottery_id}"
            )
        ])

        await query.answer(
            "❌ ابتدا عضو کانال شوید.",
            show_alert=True
        )

        await query.message.reply_text(
            "📢 ابتدا در کانال زیر عضو شوید:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

        return

    # ثبت کاربر
    con = db()

    try:

        con.execute(
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

        con.commit()

        await query.answer(
            "🎉 شما با موفقیت در قرعه‌کشی شرکت کردید!",
            show_alert=True
        )

    except sqlite3.IntegrityError:

        await query.answer(
            "ℹ️ شما قبلاً شرکت کرده‌اید.",
            show_alert=True
        )

    finally:
        con.close()


# =========================
# NEW LOTTERY
# =========================

async def new_lottery(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_admin(update.effective_user.id):
        return

    context.user_data.clear()
    context.user_data["step"] = "title"

    await update.message.reply_text(
        "🎁 عنوان جایزه را بفرست:\n\n"
        "مثال:\n"
        "1,000,000 میو"
    )


# =========================
# ADMIN INPUT
# =========================

async def admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message:
        return

    if not is_admin(update.effective_user.id):
        return

    step = context.user_data.get("step")

    if not step:
        return

    value = update.message.text.strip()

    # عنوان
    if step == "title":

        context.user_data["title"] = value
        context.user_data["step"] = "winners"

        await update.message.reply_text(
            "🏆 چند برنده می‌خواهی؟\n\n"
            "مثال: 1"
        )
        return

    # تعداد برنده
    if step == "winners":

        try:
            winners = int(value)

            if winners < 1:
                raise ValueError

        except ValueError:
            await update.message.reply_text(
                "❌ یک عدد معتبر وارد کن."
            )
            return

        context.user_data["winners"] = winners
        context.user_data["step"] = "duration"

        await update.message.reply_text(
            "⏱ چند ساعت فعال باشد؟\n\n"
            "مثال: 24"
        )
        return

    # مدت
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
            "اگر نمی‌خواهی:\n"
            "none"
        )
        return

    # کانال
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

        con = db()

        cursor = con.execute(
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

        con.commit()
        con.close()

        lottery = get_lottery(lottery_id)

        link = (
            f"https://t.me/"
            f"{context.bot.username}"
            f"?start=lottery_{lottery_id}"
        )

        message = (
            lottery_message(lottery)
            + "\n\n"
            f"🔗 لینک شرکت:\n{link}"
        )

        try:

            await context.bot.send_message(
                chat_id=CHANNEL,
                text=message,
                parse_mode="HTML",
                reply_markup=lottery_button(
                    lottery_id
                )
            )

            await update.message.reply_text(
                "✅ قرعه‌کشی ساخته شد!\n\n"
                + message
            )

        except Exception as error:

            await update.message.reply_text(
                "❌ ارسال به کانال ناموفق بود:\n"
                f"{error}"
            )

        context.user_data.clear()


# =========================
# DRAW
# =========================

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
    except ValueError:
        await update.message.reply_text(
            "❌ شناسه نامعتبر است."
        )
        return

    lottery = get_lottery(lottery_id)

    if not lottery:
        await update.message.reply_text(
            "❌ قرعه‌کشی پیدا نشد."
        )
        return

    if lottery["status"] != "active":
        await update.message.reply_text(
            "⛔ این قرعه‌کشی قبلاً انجام شده."
        )
        return

    users = get_participants(lottery_id)

    if not users:
        await update.message.reply_text(
            "❌ هیچ شرکت‌کننده‌ای وجود ندارد."
        )
        return

    count = min(
        lottery["winners"],
        len(users)
    )

    winners = random.sample(
        users,
        count
    )

    # جلوگیری از قرعه‌کشی دوباره
    con = db()

    con.execute(
        """
        UPDATE lotteries
        SET status='drawn'
        WHERE id=?
        """,
        (lottery_id,)
    )

    con.commit()
    con.close()

    # =========================
    # RESULT
    # =========================

    result = (
        "🎊 <b>نتیجه قرعه‌کشی</b>\n\n"
        f"🎁 جایزه: "
        f"<b>{lottery['title']}</b>\n\n"
    )

    for i, winner in enumerate(
        winners,
        start=1
    ):

        name = winner["first_name"] or "بدون نام"

        if winner["username"]:
            username = "@" + winner["username"]
        else:
            username = "ندارد"

        result += (
            f"🏆 <b>برنده {i}</b>\n"
            f"👤 نام: {name}\n"
            f"🔗 یوزرنیم: {username}\n"
            f"🆔 آیدی عددی: "
            f"<code>{winner['user_id']}</code>\n\n"
        )

    # برای ادمین
    await update.message.reply_text(
        result,
        parse_mode="HTML"
    )

    # =========================
    # CHANNEL
    # =========================

    try:

        await context.bot.send_message(
            chat_id=CHANNEL,
            text=(
                "🎉 <b>قرعه‌کشی به پایان رسید!</b>\n\n"
                + result
                + "❤️ ممنون از شرکت شما"
            ),
            parse_mode="HTML"
        )

    except Exception as error:

        await update.message.reply_text(
            "⚠️ ارسال نتیجه به کانال ناموفق بود:\n"
            f"{error}"
        )

    # =========================
    # PRIVATE MESSAGE
    # =========================

    for winner in winners:

        try:

            await context.bot.send_message(
                chat_id=winner["user_id"],
                text=(
                    "🎉 <b>تبریک!</b>\n\n"
                    "🏆 شما برنده قرعه‌کشی شدید!\n\n"
                    f"🎁 جایزه: "
                    f"<b>{lottery['title']}</b>\n\n"
                    "📩 برای دریافت جایزه، "
                    "لطفاً به پیوی ادمین مراجعه کنید."
                ),
                parse_mode="HTML"
            )

        except Exception:
            pass


# =========================
# MAIN
# =========================

def main():

    if not TOKEN:
        raise RuntimeError(
            "BOT_TOKEN تنظیم نشده است."
        )

    setup_database()

    Thread(
        target=web_server,
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
            join,
            pattern=r"^join:"
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            admin_input
        )
    )

    print("MEOW LOTTERY BOT STARTED")

    application.run_polling()


if __name__ == "__main__":
    main()

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

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7111630140"))
CHANNEL = os.getenv("ANNOUNCE_CHANNEL", "@meow_lottery")

DB = "lottery.db"

app = Flask(__name__)


# =========================================================
# WEB SERVER
# =========================================================

@app.route("/")
def home():
    return "Meow Lottery Bot is running."


def web_server():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)


# =========================================================
# DATABASE
# =========================================================

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
            status TEXT DEFAULT 'active',
            message_id INTEGER
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS participants (
            lottery_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            joined_at TEXT DEFAULT '',
            UNIQUE(lottery_id, user_id)
        )
    """)

    # پشتیبانی از دیتابیس قدیمی
    lottery_columns = {
        row["name"]
        for row in con.execute(
            "PRAGMA table_info(lotteries)"
        ).fetchall()
    }

    if "message_id" not in lottery_columns:
        con.execute(
            "ALTER TABLE lotteries ADD COLUMN message_id INTEGER"
        )

    participant_columns = {
        row["name"]
        for row in con.execute(
            "PRAGMA table_info(participants)"
        ).fetchall()
    }

    if "joined_at" not in participant_columns:
        con.execute(
            "ALTER TABLE participants ADD COLUMN joined_at TEXT DEFAULT ''"
        )

    con.commit()
    con.close()


# =========================================================
# HELPERS
# =========================================================

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


def get_active_lotteries():
    con = db()

    lotteries = con.execute(
        """
        SELECT *
        FROM lotteries
        WHERE status='active'
        """
    ).fetchall()

    con.close()
    return lotteries


def get_participants(lottery_id):
    con = db()

    users = con.execute(
        """
        SELECT *
        FROM participants
        WHERE lottery_id=?
        ORDER BY joined_at ASC
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


def remaining_seconds(end_time):
    try:
        end = datetime.fromisoformat(end_time)

        if end.tzinfo is None:
            end = end.replace(
                tzinfo=timezone.utc
            )

        return max(
            0,
            int(
                (
                    end - datetime.now(timezone.utc)
                ).total_seconds()
            )
        )

    except Exception:
        return 0


def format_timer(seconds):
    if seconds <= 0:
        return "⛔ تمام شده"

    days, remainder = divmod(
        seconds,
        86400
    )

    hours, remainder = divmod(
        remainder,
        3600
    )

    minutes, seconds = divmod(
        remainder,
        60
    )

    if days:
        return (
            f"{days} روز "
            f"{hours:02d}:"
            f"{minutes:02d}:"
            f"{seconds:02d}"
        )

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{seconds:02d}"
    )


def lottery_link(lottery_id, bot_username):
    return (
        f"https://t.me/"
        f"{bot_username}"
        f"?start=lottery_{lottery_id}"
    )


def lottery_text(lottery, bot_username=None):
    count = participant_count(
        lottery["id"]
    )

    timer = format_timer(
        remaining_seconds(
            lottery["end_time"]
        )
    )

    text = (
        f"🎁 <b>{lottery['title']}</b>\n\n"
        f"🏆 تعداد برنده‌ها: "
        f"<b>{lottery['winners']}</b> نفر\n"
        f"👥 شرکت‌کنندگان: "
        f"<b>{count}</b> نفر\n"
        f"⏱ زمان باقی‌مانده: "
        f"<b>{timer}</b>\n\n"
        "👇 برای شرکت روی دکمه زیر بزنید."
    )

    if bot_username:
        text += (
            "\n\n"
            f"🔗 لینک شرکت:\n"
            f"{lottery_link(lottery['id'], bot_username)}"
        )

    return text


def lottery_button(lottery_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎰 شرکت در قرعه‌کشی",
                callback_data=f"join:{lottery_id}"
            )
        ]
    ])


def finished_button():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⛔ قرعه‌کشی تمام شده",
                callback_data="finished"
            )
        ]
    ])


# =========================================================
# START
# =========================================================

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

    if lottery["status"] != "active":
        await update.message.reply_text(
            "⛔ این قرعه‌کشی تمام شده است."
        )
        return

    if remaining_seconds(
        lottery["end_time"]
    ) <= 0:
        await update.message.reply_text(
            "⛔ زمان قرعه‌کشی تمام شده است."
        )
        return

    await update.message.reply_text(
        lottery_text(
            lottery,
            context.bot.username
        ),
        parse_mode="HTML",
        reply_markup=lottery_button(
            lottery_id
        )
    )


# =========================================================
# JOIN
# =========================================================

async def join(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    try:
        lottery_id = int(
            query.data.split(":")[1]
        )
    except (ValueError, IndexError):
        await query.answer(
            "❌ اطلاعات نامعتبر است.",
            show_alert=True
        )
        return

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

    if remaining_seconds(
        lottery["end_time"]
    ) <= 0:
        await query.answer(
            "⛔ زمان قرعه‌کشی تمام شده.",
            show_alert=True
        )
        return

    # -----------------------------------------------------
    # REQUIRED CHANNEL MEMBERSHIP
    # -----------------------------------------------------

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
            reply_markup=InlineKeyboardMarkup(
                buttons
            )
        )

        return

    # -----------------------------------------------------
    # REGISTER
    # -----------------------------------------------------

    con = db()

    try:

        con.execute(
            """
            INSERT INTO participants
            (
                lottery_id,
                user_id,
                username,
                first_name,
                joined_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                lottery_id,
                query.from_user.id,
                query.from_user.username or "",
                query.from_user.first_name or "",
                datetime.now(
                    timezone.utc
                ).isoformat()
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


# =========================================================
# NEW LOTTERY
# =========================================================

async def new_lottery(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_admin(
        update.effective_user.id
    ):
        return

    context.user_data.clear()
    context.user_data["step"] = "title"

    await update.message.reply_text(
        "🎁 عنوان جایزه را بفرست:\n\n"
        "مثال:\n"
        "1,000,000 میو"
    )


# =========================================================
# ADMIN INPUT
# =========================================================

async def admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message:
        return

    if not is_admin(
        update.effective_user.id
    ):
        return

    step = context.user_data.get("step")

    if not step:
        return

    value = update.message.text.strip()

    # TITLE
    if step == "title":

        context.user_data["title"] = value
        context.user_data["step"] = "winners"

        await update.message.reply_text(
            "🏆 چند برنده می‌خواهی؟\n\n"
            "مثال: 1"
        )
        return

    # WINNERS
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

    # DURATION
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

    # CHANNEL
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
                channels,
                status
            )
            VALUES (?, ?, ?, ?, 'active')
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

        try:

            bot_username = context.bot.username

            message = lottery_text(
                lottery,
                bot_username
            )

            sent = await context.bot.send_message(
                chat_id=CHANNEL,
                text=message,
                parse_mode="HTML",
                reply_markup=lottery_button(
                    lottery_id
                )
            )

            con = db()

            con.execute(
                """
                UPDATE lotteries
                SET message_id=?
                WHERE id=?
                """,
                (
                    sent.message_id,
                    lottery_id
                )
            )

            con.commit()
            con.close()

            await update.message.reply_text(
                "✅ قرعه‌کشی ساخته شد!\n\n"
                f"🆔 شناسه: {lottery_id}\n"
                f"🎁 جایزه: {title}\n"
                f"🏆 برنده‌ها: {winners}\n"
                f"⏱ مدت: {hours} ساعت"
            )

        except Exception as error:

            await update.message.reply_text(
                "❌ ارسال به کانال ناموفق بود:\n"
                f"{error}"
            )

        context.user_data.clear()


# =========================================================
# PARTICIPANTS
# =========================================================

async def participants_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    if not context.args:

        await update.message.reply_text(
            "مثال:\n"
            "/participants 1"
        )
        return

    try:
        lottery_id = int(
            context.args[0]
        )
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

    users = get_participants(lottery_id)

    if not users:

        await update.message.reply_text(
            "📋 هیچ شرکت‌کننده‌ای وجود ندارد."
        )
        return

    text = (
        "📋 <b>لیست شرکت‌کنندگان</b>\n\n"
        f"🎁 {lottery['title']}\n"
        f"👥 تعداد: <b>{len(users)}</b>\n\n"
    )

    for index, user in enumerate(
        users,
        start=1
    ):

        name = (
            user["first_name"]
            or "بدون نام"
        )

        username = (
            f"@{user['username']}"
            if user["username"]
            else "ندارد"
        )

        item = (
            f"{index}. 👤 {name}\n"
            f"   🔗 {username}\n"
            f"   🆔 <code>{user['user_id']}</code>\n\n"
        )

        if len(text) + len(item) > 3800:

            await update.message.reply_text(
                text,
                parse_mode="HTML"
            )

            text = ""

        text += item

    if text:

        await update.message.reply_text(
            text,
            parse_mode="HTML"
        )


# =========================================================
# DRAW
# =========================================================

async def perform_draw(
    lottery_id,
    context
):

    lottery = get_lottery(lottery_id)

    if not lottery:
        return False

    if lottery["status"] != "active":
        return False

    users = get_participants(lottery_id)

    # هیچ شرکت‌کننده‌ای
    if not users:

        con = db()

        cursor = con.execute(
            """
            UPDATE lotteries
            SET status='drawn'
            WHERE id=? AND status='active'
            """,
            (lottery_id,)
        )

        con.commit()
        con.close()

        if cursor.rowcount == 0:
            return False

        try:

            await context.bot.send_message(
                chat_id=CHANNEL,
                text=(
                    "⛔ <b>قرعه‌کشی به پایان رسید</b>\n\n"
                    f"🎁 {lottery['title']}\n\n"
                    "هیچ شرکت‌کننده‌ای وجود نداشت."
                ),
                parse_mode="HTML"
            )

        except Exception as error:

            print(
                f"Empty lottery error: {error}"
            )

        return True

    # تعداد برنده هیچ‌وقت بیشتر از شرکت‌کننده نیست
    winner_count = min(
        int(lottery["winners"]),
        len(users)
    )

    winners = random.sample(
        users,
        winner_count
    )

    # قفل کردن قرعه‌کشی
    con = db()

    cursor = con.execute(
        """
        UPDATE lotteries
        SET status='drawn'
        WHERE id=? AND status='active'
        """,
        (lottery_id,)
    )

    con.commit()
    con.close()

    if cursor.rowcount == 0:
        return False

    # -----------------------------------------------------
    # RESULT
    # -----------------------------------------------------

    result = (
        "🎊 <b>نتیجه قرعه‌کشی</b>\n\n"
        f"🎁 جایزه: "
        f"<b>{lottery['title']}</b>\n"
        f"👥 شرکت‌کنندگان: "
        f"<b>{len(users)}</b>\n"
        f"🏆 برنده‌ها: "
        f"<b>{winner_count}</b>\n\n"
    )

    for index, winner in enumerate(
        winners,
        start=1
    ):

        name = (
            winner["first_name"]
            or "بدون نام"
        )

        username = (
            f"@{winner['username']}"
            if winner["username"]
            else "ندارد"
        )

        result += (
            f"🏆 <b>برنده {index}</b>\n"
            f"👤 نام: {name}\n"
            f"🔗 یوزرنیم: {username}\n"
            f"🆔 آیدی عددی: "
            f"<code>{winner['user_id']}</code>\n\n"
        )

    # -----------------------------------------------------
    # SEND TO ADMIN
    # -----------------------------------------------------

    try:

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=result,
            parse_mode="HTML"
        )

    except Exception as error:

        print(
            f"Admin result error: {error}"
        )

    # -----------------------------------------------------
    # SEND NEW RESULT MESSAGE TO CHANNEL
    # -----------------------------------------------------

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

        print(
            f"Channel result error: {error}"
        )

    # -----------------------------------------------------
    # PRIVATE MESSAGE TO WINNERS
    # -----------------------------------------------------

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

        except Exception as error:

            print(
                f"Winner DM error "
                f"{winner['user_id']}: {error}"
            )

    # -----------------------------------------------------
    # UPDATE ORIGINAL LOTTERY MESSAGE
    # -----------------------------------------------------

    if lottery["message_id"]:

        try:

            await context.bot.edit_message_text(
                chat_id=CHANNEL,
                message_id=lottery["message_id"],
                text=(
                    f"🎁 <b>{lottery['title']}</b>\n\n"
                    f"🏆 تعداد برنده‌ها: "
                    f"<b>{winner_count}</b>\n"
                    f"👥 شرکت‌کنندگان: "
                    f"<b>{len(users)}</b>\n"
                    f"⏱ زمان باقی‌مانده: "
                    f"<b>⛔ تمام شد</b>\n\n"
                    "🎉 قرعه‌کشی انجام شد."
                ),
                parse_mode="HTML",
                reply_markup=finished_button()
            )

        except Exception as error:

            print(
                f"Original message error: {error}"
            )

    return True


# =========================================================
# MANUAL DRAW
# =========================================================

async def draw(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(
        update.effective_user.id
    ):
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
            "❌ شناسه نامعتبر است."
        )
        return

    success = await perform_draw(
        lottery_id,
        context
    )

    if success:

        await update.message.reply_text(
            "✅ قرعه‌کشی انجام شد."
        )

    else:

        await update.message.reply_text(
            "❌ قرعه‌کشی انجام نشد."
        )


# =========================================================
# AUTO DRAW
# =========================================================

async def auto_draw_job(
    context: ContextTypes.DEFAULT_TYPE
):

    lotteries = get_active_lotteries()

    now = datetime.now(
        timezone.utc
    )

    for lottery in lotteries:

        try:

            end_time = datetime.fromisoformat(
                lottery["end_time"]
            )

            if end_time.tzinfo is None:
                end_time = end_time.replace(
                    tzinfo=timezone.utc
                )

            if now >= end_time:

                await perform_draw(
                    lottery["id"],
                    context
                )

        except Exception as error:

            print(
                f"Auto draw error "
                f"{lottery['id']}: {error}"
            )


# =========================================================
# UPDATE TIMER + PARTICIPANTS
# =========================================================

async def update_lottery_messages(
    context: ContextTypes.DEFAULT_TYPE
):

    lotteries = get_active_lotteries()

    bot_username = context.bot.username

    if not bot_username:
        return

    for lottery in lotteries:

        message_id = lottery["message_id"]

        if not message_id:
            continue

        seconds = remaining_seconds(
            lottery["end_time"]
        )

        if seconds <= 0:
            continue

        try:

            # متن از نو ساخته می‌شود تا:
            # 1. لینک حذف نشود
            # 2. دکمه حذف نشود
            # 3. تعداد شرکت‌کننده آپدیت شود
            # 4. تایمر دقیق بماند

            text = lottery_text(
                lottery,
                bot_username
            )

            await context.bot.edit_message_text(
                chat_id=CHANNEL,
                message_id=message_id,
                text=text,
                parse_mode="HTML",
                reply_markup=lottery_button(
                    lottery["id"]
                )
            )

        except Exception as error:

            print(
                f"Timer/message update error "
                f"{lottery['id']}: {error}"
            )


# =========================================================
# FINISHED BUTTON
# =========================================================

async def finished_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.callback_query.answer(
        "⛔ این قرعه‌کشی تمام شده است.",
        show_alert=True
    )


# =========================================================
# MAIN
# =========================================================

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

    # -----------------------------------------------------
    # AUTO JOBS
    # -----------------------------------------------------

    if application.job_queue:

        # بررسی پایان قرعه‌کشی هر 10 ثانیه
        application.job_queue.run_repeating(
            auto_draw_job,
            interval=10,
            first=5
        )

        # آپدیت تایمر و تعداد شرکت‌کنندگان هر 10 ثانیه
        application.job_queue.run_repeating(
            update_lottery_messages,
            interval=10,
            first=10
        )

    # -----------------------------------------------------
    # COMMANDS
    # -----------------------------------------------------

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
        CommandHandler(
            "participants",
            participants_command
        )
    )

    # -----------------------------------------------------
    # CALLBACKS
    # -----------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            join,
            pattern=r"^join:"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            finished_callback,
            pattern=r"^finished$"
        )
    )

    # -----------------------------------------------------
    # ADMIN TEXT INPUT
    # -----------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            admin_input
        )
    )

    print("================================")
    print("MEOW LOTTERY BOT STARTED")
    print("AUTO DRAW: ON")
    print("TIMER UPDATE: 10 SEC")
    print("PARTICIPANT UPDATE: 10 SEC")
    print("================================")

    application.run_polling()


if __name__ == "__main__":
    main()

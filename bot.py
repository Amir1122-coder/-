import os
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from threading import Thread

from flask import Flask

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
    MessageHandler,
    filters,
)

from telegram.constants import ChatMemberStatus


# ============================================================
# CONFIG
# ============================================================

TOKEN = os.getenv("BOT_TOKEN")

ADMIN_ID = int(
    os.getenv("ADMIN_ID", "7111630140")
)

CHANNEL = os.getenv(
    "ANNOUNCE_CHANNEL",
    "@meow_lottery"
)

DB = "lottery.db"

UPDATE_INTERVAL = 3600


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "MEOW LOTTERY BOT IS RUNNING."


def web_server():

    port = int(
        os.getenv("PORT", "10000")
    )

    app.run(
        host="0.0.0.0",
        port=port
    )


# ============================================================
# DATABASE
# ============================================================

def db():

    con = sqlite3.connect(
        DB,
        check_same_thread=False
    )

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

            channel_message_id INTEGER DEFAULT NULL,

            created_at TEXT DEFAULT NULL

        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS participants (

            lottery_id INTEGER,

            user_id INTEGER,

            username TEXT DEFAULT '',

            first_name TEXT DEFAULT '',

            joined_at TEXT DEFAULT NULL,

            UNIQUE(lottery_id, user_id)

        )
    """)

    con.commit()

    con.close()


# ============================================================
# DATABASE MIGRATION
# ============================================================

def migrate_database():

    con = db()

    columns = con.execute(
        "PRAGMA table_info(lotteries)"
    ).fetchall()

    column_names = [
        column["name"]
        for column in columns
    ]

    if "channel_message_id" not in column_names:

        con.execute("""
            ALTER TABLE lotteries
            ADD COLUMN channel_message_id INTEGER DEFAULT NULL
        """)

    if "created_at" not in column_names:

        con.execute("""
            ALTER TABLE lotteries
            ADD COLUMN created_at TEXT DEFAULT NULL
        """)

    participant_columns = con.execute(
        "PRAGMA table_info(participants)"
    ).fetchall()

    participant_names = [
        column["name"]
        for column in participant_columns
    ]

    if "joined_at" not in participant_names:

        con.execute("""
            ALTER TABLE participants
            ADD COLUMN joined_at TEXT DEFAULT NULL
        """)

    con.commit()

    con.close()


# ============================================================
# HELPERS
# ============================================================

def is_admin(user_id):

    return user_id == ADMIN_ID


def now_utc():

    return datetime.now(
        timezone.utc
    )


def get_lottery(lottery_id):

    con = db()

    lottery = con.execute(
        """
        SELECT *
        FROM lotteries
        WHERE id=?
        """,
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
        ORDER BY id DESC
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
        ORDER BY rowid ASC
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


# ============================================================
# TIME
# ============================================================

def remaining_seconds(end_time):

    end = datetime.fromisoformat(
        end_time
    )

    return int(
        (
            end - now_utc()
        ).total_seconds()
    )


def remaining_time(end_time):

    seconds = remaining_seconds(
        end_time
    )

    if seconds <= 0:

        return "⛔ تمام شده"

    days, seconds = divmod(
        seconds,
        86400
    )

    hours, seconds = divmod(
        seconds,
        3600
    )

    minutes, seconds = divmod(
        seconds,
        60
    )

    if days:

        return (
            f"{days} روز "
            f"{hours:02d}:{minutes:02d}:"
            f"{seconds:02d}"
        )

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{seconds:02d}"
    )


# ============================================================
# CHANNEL URL
# ============================================================

def channel_username():

    return CHANNEL.replace(
        "https://t.me/",
        ""
    ).replace(
        "@",
        ""
    ).strip()


def channel_url():

    return (
        f"https://t.me/"
        f"{channel_username()}"
    )


# ============================================================
# LOTTERY MESSAGE
# ============================================================

def lottery_message(lottery):

    count = participant_count(
        lottery["id"]
    )

    return (
        f"🎁 <b>{lottery['title']}</b>\n\n"

        f"🏆 تعداد برنده‌ها: "
        f"<b>{lottery['winners']}</b> نفر\n"

        f"👥 شرکت‌کنندگان: "
        f"<b>{count}</b> نفر\n"

        f"⏱ زمان باقی‌مانده: "
        f"<b>{remaining_time(lottery['end_time'])}</b>\n\n"

        "👇 برای شرکت در قرعه‌کشی "
        "روی دکمه زیر بزنید."
    )


# ============================================================
# LOTTERY BUTTON
# ============================================================

def lottery_button(lottery_id):

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎰 شرکت در قرعه‌کشی",
                callback_data=f"join:{lottery_id}"
            )
        ]
    ])


# ============================================================
# START BUTTON
# ============================================================

def welcome_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📢 عضویت در کانال",
                url=channel_url()
            )
        ]
    ])


# ============================================================
# ADMIN PANEL
# ============================================================

def admin_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "🎰 قرعه‌کشی جدید",
                callback_data="admin:new"
            )
        ],

        [
            InlineKeyboardButton(
                "📋 قرعه‌کشی‌های فعال",
                callback_data="admin:list"
            )
        ],

        [
            InlineKeyboardButton(
                "👥 شرکت‌کنندگان",
                callback_data="admin:participants"
            )
        ],

        [
            InlineKeyboardButton(
                "🎲 اجرای قرعه‌کشی",
                callback_data="admin:draw"
            )
        ],

        [
            InlineKeyboardButton(
                "❌ لغو قرعه‌کشی",
                callback_data="admin:cancel"
            )
        ],

        [
            InlineKeyboardButton(
                "🔄 بروزرسانی",
                callback_data="admin:panel"
            )
        ]

    ])


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    # --------------------------------------------------------
    # NORMAL START
    # --------------------------------------------------------

    if not context.args:

        await update.message.reply_text(

            "🎰 <b>به لاتاری میویی خوش اومدید!</b>\n\n"

            "✨ برای اجرای ربات باید ابتدا "
            "در کانال زیر عضو شوید.\n\n"

            f"📢 کانال:\n"
            f"{channel_url()}\n\n"

            "👇 بعد از عضویت می‌توانید "
            "از ربات استفاده کنید.",

            parse_mode="HTML",

            reply_markup=welcome_keyboard()
        )

        return

    # --------------------------------------------------------
    # LOTTERY DEEP LINK
    # --------------------------------------------------------

    arg = context.args[0]

    if not arg.startswith("lottery_"):

        await update.message.reply_text(

            "🎰 <b>به لاتاری میویی خوش اومدید!</b>\n\n"

            "برای اجرای ربات ابتدا "
            "در کانال عضو شوید.",

            parse_mode="HTML",

            reply_markup=welcome_keyboard()
        )

        return

    try:

        lottery_id = int(
            arg.replace(
                "lottery_",
                ""
            )
        )

    except ValueError:

        await update.message.reply_text(
            "❌ لینک قرعه‌کشی نامعتبر است."
        )

        return

    lottery = get_lottery(
        lottery_id
    )

    if not lottery:

        await update.message.reply_text(
            "❌ قرعه‌کشی پیدا نشد."
        )

        return

    if lottery["status"] != "active":

        await update.message.reply_text(
            "⛔ این قرعه‌کشی فعال نیست."
        )

        return

    if remaining_seconds(
        lottery["end_time"]
    ) <= 0:

        await update.message.reply_text(
            "⛔ زمان این قرعه‌کشی تمام شده است."
        )

        return

    await update.message.reply_text(

        lottery_message(
            lottery
        ),

        parse_mode="HTML",

        reply_markup=lottery_button(
            lottery_id
        )
    )


# ============================================================
# JOIN LOTTERY
# ============================================================

async def join(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    try:

        lottery_id = int(
            query.data.split(":")[1]
        )

    except Exception:

        await query.answer(
            "❌ خطا.",
            show_alert=True
        )

        return

    lottery = get_lottery(
        lottery_id
    )

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

    user = query.from_user

    # --------------------------------------------------------
    # REQUIRED CHANNEL CHECK
    # --------------------------------------------------------

    missing = []

    channels = (
        lottery["channels"]
        or ""
    )

    for channel in channels.split(","):

        channel = channel.strip()

        if not channel:
            continue

        try:

            member = (
                await context.bot
                .get_chat_member(
                    chat_id=channel,
                    user_id=user.id
                )
            )

            if member.status in (
                ChatMemberStatus.LEFT,
                ChatMemberStatus.KICKED
            ):

                missing.append(
                    channel.replace("@", "")
                )

        except Exception:

            missing.append(
                channel.replace("@", "")
            )

    if missing:

        buttons = []

        for channel in missing:

            buttons.append([
                InlineKeyboardButton(
                    f"📢 عضویت @{channel}",
                    url=(
                        f"https://t.me/"
                        f"{channel}"
                    )
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                "🔄 بررسی عضویت",
                callback_data=(
                    f"join:{lottery_id}"
                )
            )
        ])

        await query.answer(
            "❌ ابتدا عضو کانال شوید.",
            show_alert=True
        )

        try:

            await context.bot.send_message(

                chat_id=user.id,

                text=(
                    "📢 برای شرکت در این "
                    "قرعه‌کشی ابتدا باید "
                    "در کانال زیر عضو شوید."
                ),

                reply_markup=(
                    InlineKeyboardMarkup(
                        buttons
                    )
                )
            )

        except Exception:

            await query.answer(
                "ابتدا ربات را در پیوی Start کنید.",
                show_alert=True
            )

        return

    # --------------------------------------------------------
    # REGISTER USER
    # --------------------------------------------------------

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
                user.id,
                user.username or "",
                user.first_name or "",
                now_utc().isoformat()
            )
        )

        con.commit()

        # پیام خصوصی
        try:

            await context.bot.send_message(

                chat_id=user.id,

                text=(
                    "🎉 <b>تبریک!</b>\n\n"

                    "شما با موفقیت در "
                    "قرعه‌کشی شرکت کردید. ✅\n\n"

                    f"🎁 جایزه:\n"
                    f"<b>{lottery['title']}</b>\n\n"

                    "🍀 موفق باشید!"
                ),

                parse_mode="HTML"
            )

        except Exception:
            pass

        await query.answer(
            "🎉 شما با موفقیت شرکت کردید!",
            show_alert=True
        )

    except sqlite3.IntegrityError:

        await query.answer(
            "ℹ️ شما قبلاً در این قرعه‌کشی شرکت کرده‌اید.",
            show_alert=True
        )

    finally:

        con.close()


# ============================================================
# NEW LOTTERY
# ============================================================

async def new_lottery(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    context.user_data.clear()

    context.user_data["step"] = "title"

    await update.message.reply_text(

        "🎁 <b>قرعه‌کشی جدید</b>\n\n"

        "عنوان جایزه را بفرست:\n\n"

        "مثال:\n"
        "1,000,000 میو",

        parse_mode="HTML"
    )


# ============================================================
# ADMIN INPUT
# ============================================================

async def admin_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    if not is_admin(
        update.effective_user.id
    ):
        return

    step = context.user_data.get(
        "step"
    )

    if not step:
        return

    value = (
        update.message.text
        .strip()
    )

    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    if step == "title":

        context.user_data[
            "title"
        ] = value

        context.user_data[
            "step"
        ] = "winners"

        await update.message.reply_text(

            "🏆 چند برنده می‌خواهی؟\n\n"
            "مثال:\n"
            "3"
        )

        return

    # --------------------------------------------------------
    # WINNERS
    # --------------------------------------------------------

    if step == "winners":

        try:

            winners = int(value)

            if winners < 1:
                raise ValueError

        except ValueError:

            await update.message.reply_text(
                "❌ یک عدد صحیح معتبر وارد کن."
            )

            return

        context.user_data[
            "winners"
        ] = winners

        context.user_data[
            "step"
        ] = "duration"

        await update.message.reply_text(

            "⏱ قرعه‌کشی چند ساعت فعال باشد؟\n\n"

            "مثال:\n"
            "24"
        )

        return

    # --------------------------------------------------------
    # DURATION
    # --------------------------------------------------------

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

        context.user_data[
            "hours"
        ] = hours

        context.user_data[
            "step"
        ] = "channels"

        await update.message.reply_text(

            "📢 کانال‌های عضویت اجباری را بفرست.\n\n"

            "مثال:\n"
            "@channel1,@channel2\n\n"

            "اگر کانال اجباری نداری:\n"
            "none"
        )

        return

    # --------------------------------------------------------
    # CHANNELS
    # --------------------------------------------------------

    if step == "channels":

        channels = ""

        if value.lower() != "none":

            channels = value

        title = context.user_data[
            "title"
        ]

        winners = context.user_data[
            "winners"
        ]

        hours = context.user_data[
            "hours"
        ]

        end_time = (

            now_utc()

            + timedelta(
                hours=hours
            )

        ).isoformat()

        created_at = (
            now_utc().isoformat()
        )

        con = db()

        cursor = con.execute(

            """
            INSERT INTO lotteries
            (
                title,
                winners,
                end_time,
                channels,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, 'active', ?)
            """,

            (
                title,
                winners,
                end_time,
                channels,
                created_at
            )
        )

        lottery_id = cursor.lastrowid

        con.commit()

        con.close()

        lottery = get_lottery(
            lottery_id
        )

        # ----------------------------------------------------
        # CHANNEL MESSAGE
        # ----------------------------------------------------

        try:

            sent = (
                await context.bot
                .send_message(

                    chat_id=CHANNEL,

                    text=lottery_message(
                        lottery
                    ),

                    parse_mode="HTML",

                    reply_markup=(
                        lottery_button(
                            lottery_id
                        )
                    )
                )
            )

            # ذخیره message_id
            con = db()

            con.execute(

                """
                UPDATE lotteries
                SET channel_message_id=?
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

                "✅ قرعه‌کشی با موفقیت ساخته شد.\n\n"

                f"🆔 شناسه: <code>{lottery_id}</code>\n"
                f"🎁 جایزه: {title}\n"
                f"🏆 برنده‌ها: {winners}\n"
                f"⏱ مدت: {hours} ساعت",

                parse_mode="HTML"
            )

        except Exception as error:

            await update.message.reply_text(

                "❌ ارسال به کانال ناموفق بود:\n\n"
                f"<code>{error}</code>",

                parse_mode="HTML"
            )

        context.user_data.clear()


# ============================================================
# DRAW FUNCTION
# ============================================================

async def perform_draw(
    lottery_id,
    context,
    admin_chat_id=None
):

    lottery = get_lottery(
        lottery_id
    )

    if not lottery:
        return False

    if lottery["status"] != "active":
        return False

    users = get_participants(
        lottery_id
    )

    if not users:

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

        result = (

            "🎊 <b>قرعه‌کشی به پایان رسید!</b>\n\n"

            f"🎁 جایزه:\n"
            f"<b>{lottery['title']}</b>\n\n"

            "❌ متأسفانه هیچ شرکت‌کننده‌ای "
            "در قرعه‌کشی وجود نداشت."
        )

        try:

            await context.bot.send_message(
                chat_id=CHANNEL,
                text=result,
                parse_mode="HTML"
            )

        except Exception:
            pass

        return True

    count = min(
        int(lottery["winners"]),
        len(users)
    )

    winners = random.sample(
        users,
        count
    )

    # --------------------------------------------------------
    # MARK DRAWN
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # RESULT
    # --------------------------------------------------------

    result = (

        "🎊 <b>نتیجه قرعه‌کشی</b>\n\n"

        f"🎁 جایزه:\n"
        f"<b>{lottery['title']}</b>\n\n"
    )

    for index, winner in enumerate(
        winners,
        start=1
    ):

        name = (
            winner["first_name"]
            or "بدون نام"
        )

        if winner["username"]:

            username = (
                "@"
                + winner["username"]
            )

        else:

            username = "ندارد"

        result += (

            f"🏆 <b>برنده {index}</b>\n"

            f"👤 نام: {name}\n"

            f"🔗 یوزرنیم: {username}\n"

            f"🆔 آیدی عددی: "
            f"<code>{winner['user_id']}</code>\n\n"
        )

    result += (
        "❤️ ممنون از شرکت شما"
    )

    # --------------------------------------------------------
    # ADMIN
    # --------------------------------------------------------

    if admin_chat_id:

        try:

            await context.bot.send_message(

                chat_id=admin_chat_id,

                text=result,

                parse_mode="HTML"
            )

        except Exception:
            pass

    # --------------------------------------------------------
    # CHANNEL
    # --------------------------------------------------------

    try:

        await context.bot.send_message(

            chat_id=CHANNEL,

            text=result,

            parse_mode="HTML"
        )

    except Exception:
        pass

    # --------------------------------------------------------
    # PRIVATE WINNER MESSAGE
    # --------------------------------------------------------

    for winner in winners:

        try:

            await context.bot.send_message(

                chat_id=winner["user_id"],

                text=(

                    "🎉 <b>تبریک!</b>\n\n"

                    "🏆 شما برنده قرعه‌کشی شدید! 🎊\n\n"

                    f"🎁 جایزه:\n"
                    f"<b>{lottery['title']}</b>\n\n"

                    "📩 برای دریافت جایزه، "
                    "لطفاً به پیوی ادمین مراجعه کنید."
                ),

                parse_mode="HTML"
            )

        except Exception:
            pass

    return True


# ============================================================
# MANUAL DRAW
# ============================================================

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
            "مثال:\n/draw 1"
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

    lottery = get_lottery(
        lottery_id
    )

    if not lottery:

        await update.message.reply_text(
            "❌ قرعه‌کشی پیدا نشد."
        )

        return

    if lottery["status"] != "active":

        await update.message.reply_text(
            "⛔ این قرعه‌کشی قبلاً پایان یافته."
        )

        return

    success = await perform_draw(

        lottery_id,

        context,

        update.effective_chat.id
    )

    if success:

        await update.message.reply_text(
            "✅ قرعه‌کشی انجام شد."
        )


# ============================================================
# PARTICIPANTS COMMAND
# ============================================================

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
            "مثال:\n/participants 1"
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

    lottery = get_lottery(
        lottery_id
    )

    if not lottery:

        await update.message.reply_text(
            "❌ قرعه‌کشی پیدا نشد."
        )

        return

    users = get_participants(
        lottery_id
    )

    if not users:

        await update.message.reply_text(
            "👥 هنوز کسی شرکت نکرده."
        )

        return

    text = (

        f"👥 <b>شرکت‌کنندگان</b>\n\n"

        f"🎁 {lottery['title']}\n"

        f"📊 تعداد: {len(users)}\n\n"
    )

    for index, user in enumerate(
        users,
        start=1
    ):

        username = (

            "@"
            + user["username"]

            if user["username"]

            else "ندارد"
        )

        text += (

            f"{index}. "

            f"👤 {user['first_name'] or 'بدون نام'}\n"

            f"🔗 {username}\n"

            f"🆔 <code>{user['user_id']}</code>\n\n"
        )

        # جلوگیری از عبور از محدودیت پیام تلگرام
        if len(text) > 3500:

            await update.message.reply_text(

                text,

                parse_mode="HTML"
            )

            text = ""

    if text:

        await update.message.reply_text(

            text,

            parse_mode="HTML"
        )


# ============================================================
# LIST ACTIVE LOTTERIES
# ============================================================

async def list_lotteries(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    lotteries = get_active_lotteries()

    if not lotteries:

        await update.message.reply_text(
            "📭 هیچ قرعه‌کشی فعالی وجود ندارد."
        )

        return

    text = "📋 <b>قرعه‌کشی‌های فعال</b>\n\n"

    for lottery in lotteries:

        count = participant_count(
            lottery["id"]
        )

        text += (

            f"🆔 <b>{lottery['id']}</b>\n"

            f"🎁 {lottery['title']}\n"

            f"🏆 برنده‌ها: "
            f"{lottery['winners']}\n"

            f"👥 شرکت‌کنندگان: "
            f"{count}\n"

            f"⏱ باقی‌مانده: "
            f"{remaining_time(lottery['end_time'])}\n\n"
        )

    await update.message.reply_text(

        text,

        parse_mode="HTML"
    )


# ============================================================
# CANCEL
# ============================================================

async def cancel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    if not context.args:

        await update.message.reply_text(
            "مثال:\n/cancel 1"
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

    lottery = get_lottery(
        lottery_id
    )

    if not lottery:

        await update.message.reply_text(
            "❌ قرعه‌کشی پیدا نشد."
        )

        return

    if lottery["status"] != "active":

        await update.message.reply_text(
            "⛔ این قرعه‌کشی فعال نیست."
        )

        return

    keyboard = InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "✅ بله، لغو شود",
                callback_data=(
                    f"cancel_yes:{lottery_id}"
                )
            )
        ],

        [
            InlineKeyboardButton(
                "❌ خیر",
                callback_data="cancel_no"
            )
        ]

    ])

    await update.message.reply_text(

        "⚠️ <b>تأیید لغو قرعه‌کشی</b>\n\n"

        f"🎁 {lottery['title']}\n\n"

        "آیا مطمئنی می‌خواهی "
        "این قرعه‌کشی لغو شود؟",

        parse_mode="HTML",

        reply_markup=keyboard
    )


# ============================================================
# CANCEL CONFIRMATION
# ============================================================

async def cancel_yes(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if not is_admin(
        query.from_user.id
    ):

        await query.answer(
            "❌ دسترسی ندارید.",
            show_alert=True
        )

        return

    lottery_id = int(
        query.data.split(":")[1]
    )

    lottery = get_lottery(
        lottery_id
    )

    if not lottery:

        await query.answer(
            "❌ پیدا نشد.",
            show_alert=True
        )

        return

    if lottery["status"] != "active":

        await query.answer(
            "⛔ قبلاً پایان یافته.",
            show_alert=True
        )

        return

    con = db()

    con.execute(

        """
        UPDATE lotteries
        SET status='cancelled'
        WHERE id=?
        """,

        (lottery_id,)
    )

    con.commit()

    con.close()

    # --------------------------------------------------------
    # EDIT CHANNEL MESSAGE
    # --------------------------------------------------------

    if lottery["channel_message_id"]:

        try:

            await context.bot.edit_message_text(

                chat_id=CHANNEL,

                message_id=(
                    lottery["channel_message_id"]
                ),

                text=(

                    f"❌ <b>قرعه‌کشی لغو شد</b>\n\n"

                    f"🎁 جایزه:\n"
                    f"<b>{lottery['title']}</b>\n\n"

                    "⛔ این قرعه‌کشی دیگر "
                    "قابل شرکت نیست."
                ),

                parse_mode="HTML",

                reply_markup=None
            )

        except Exception:
            pass

    await query.answer(
        "✅ قرعه‌کشی لغو شد."
    )

    await query.edit_message_text(

        "✅ قرعه‌کشی با موفقیت لغو شد.",

        parse_mode="HTML"
    )


async def cancel_no(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if not is_admin(
        query.from_user.id
    ):
        return

    await query.answer(
        "لغو عملیات"
    )

    await query.edit_message_text(
        "❌ عملیات لغو شد."
    )


# ============================================================
# ADMIN CALLBACK
# ============================================================

async def admin_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if not is_admin(
        query.from_user.id
    ):

        await query.answer(
            "❌ دسترسی ندارید.",
            show_alert=True
        )

        return

    action = query.data

    await query.answer()

    # --------------------------------------------------------
    # PANEL
    # --------------------------------------------------------

    if action == "admin:panel":

        await query.edit_message_text(

            "⚙️ <b>پنل مدیریت لاتاری میویی</b>\n\n"
            "یکی از گزینه‌های زیر را انتخاب کن:",

            parse_mode="HTML",

            reply_markup=admin_keyboard()
        )

        return

    # --------------------------------------------------------
    # NEW
    # --------------------------------------------------------

    if action == "admin:new":

        context.user_data.clear()

        context.user_data[
            "step"
        ] = "title"

        await query.message.reply_text(

            "🎁 عنوان جایزه را بفرست:"
        )

        return

    # --------------------------------------------------------
    # LIST
    # --------------------------------------------------------

    if action == "admin:list":

        lotteries = get_active_lotteries()

        if not lotteries:

            await query.message.reply_text(
                "📭 قرعه‌کشی فعالی وجود ندارد."
            )

            return

        text = (
            "📋 <b>قرعه‌کشی‌های فعال</b>\n\n"
        )

        for lottery in lotteries:

            text += (

                f"🆔 {lottery['id']}\n"
                f"🎁 {lottery['title']}\n"
                f"👥 {participant_count(lottery['id'])}\n"
                f"⏱ {remaining_time(lottery['end_time'])}\n\n"
            )

        await query.message.reply_text(

            text,

            parse_mode="HTML"
        )

        return

    # --------------------------------------------------------
    # DRAW
    # --------------------------------------------------------

    if action == "admin:draw":

        await query.message.reply_text(

            "🎲 برای اجرای دستی:\n\n"
            "/draw ID\n\n"
            "مثال:\n"
            "/draw 1"
        )

        return

    # --------------------------------------------------------
    # PARTICIPANTS
    # --------------------------------------------------------

    if action == "admin:participants":

        await query.message.reply_text(

            "👥 برای نمایش شرکت‌کنندگان:\n\n"
            "/participants ID\n\n"
            "مثال:\n"
            "/participants 1"
        )

        return

    # --------------------------------------------------------
    # CANCEL
    # --------------------------------------------------------

    if action == "admin:cancel":

        await query.message.reply_text(

            "❌ برای لغو:\n\n"
            "/cancel ID\n\n"
            "مثال:\n"
            "/cancel 1"
        )

        return


# ============================================================
# ADMIN PANEL COMMAND
# ============================================================

async def admin_panel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    await update.message.reply_text(

        "⚙️ <b>پنل مدیریت لاتاری میویی</b>\n\n"
        "یکی از گزینه‌ها را انتخاب کن:",

        parse_mode="HTML",

        reply_markup=admin_keyboard()
    )


# ============================================================
# UPDATE CHANNEL TIMER
# ============================================================

async def update_channel_messages(
    context: ContextTypes.DEFAULT_TYPE
):

    lotteries = get_active_lotteries()

    for lottery in lotteries:

        # ----------------------------------------------------
        # TIME ENDED
        # ----------------------------------------------------

        if remaining_seconds(
            lottery["end_time"]
        ) <= 0:

            try:

                await perform_draw(

                    lottery["id"],

                    context
                )

            except Exception as error:

                print(
                    "AUTO DRAW ERROR:",
                    error
                )

            continue

        # ----------------------------------------------------
        # UPDATE CHANNEL POST
        # ----------------------------------------------------

        if not lottery[
            "channel_message_id"
        ]:

            continue

        try:

            await context.bot.edit_message_text(

                chat_id=CHANNEL,

                message_id=(
                    lottery["channel_message_id"]
                ),

                text=lottery_message(
                    lottery
                ),

                parse_mode="HTML",

                reply_markup=(
                    lottery_button(
                        lottery["id"]
                    )
                )
            )

        except Exception as error:

            print(
                "CHANNEL UPDATE ERROR:",
                error
            )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    print(
        "BOT ERROR:",
        context.error
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not TOKEN:

        raise RuntimeError(
            "BOT_TOKEN تنظیم نشده است."
        )

    setup_database()

    migrate_database()

    # --------------------------------------------------------
    # WEB SERVER
    # --------------------------------------------------------

    Thread(
        target=web_server,
        daemon=True
    ).start()

    # --------------------------------------------------------
    # APPLICATION
    # --------------------------------------------------------

    application = (

        Application
        .builder()
        .token(TOKEN)
        .build()
    )

    # --------------------------------------------------------
    # COMMANDS
    # --------------------------------------------------------

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

    application.add_handler(

        CommandHandler(
            "list",
            list_lotteries
        )
    )

    application.add_handler(

        CommandHandler(
            "cancel",
            cancel_command
        )
    )

    application.add_handler(

        CommandHandler(
            "admin",
            admin_panel
        )
    )

    # --------------------------------------------------------
    # JOIN
    # --------------------------------------------------------

    application.add_handler(

        CallbackQueryHandler(
            join,
            pattern=r"^join:"
        )
    )

    # --------------------------------------------------------
    # ADMIN CALLBACKS
    # --------------------------------------------------------

    application.add_handler(

        CallbackQueryHandler(
            cancel_yes,
            pattern=r"^cancel_yes:"
        )
    )

    application.add_handler(

        CallbackQueryHandler(
            cancel_no,
            pattern=r"^cancel_no$"
        )
    )

    application.add_handler(

        CallbackQueryHandler(
            admin_callback,
            pattern=r"^admin:"
        )
    )

    # --------------------------------------------------------
    # ADMIN TEXT INPUT
    # --------------------------------------------------------

    application.add_handler(

        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            admin_input
        )
    )

    # --------------------------------------------------------
    # HOURLY JOB
    # --------------------------------------------------------

    application.job_queue.run_repeating(

        update_channel_messages,

        interval=UPDATE_INTERVAL,

        first=10
    )

    # --------------------------------------------------------
    # ERROR
    # --------------------------------------------------------

    application.add_error_handler(
        error_handler
    )

    print(
        "================================="
    )

    print(
        "MEOW LOTTERY BOT STARTED"
    )

    print(
        "================================="
    )

    # --------------------------------------------------------
    # RUN
    # --------------------------------------------------------

    application.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()

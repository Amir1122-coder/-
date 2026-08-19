import os
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from threading import Lock, Thread

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


# ============================================================
# CONFIG
# ============================================================

TOKEN = os.getenv("BOT_TOKEN")

ADMIN_ID = int(
    os.getenv(
        "ADMIN_ID",
        "7111630140"
    )
)

ADMIN_USERNAME = os.getenv(
    "ADMIN_USERNAME",
    "@your_AmiRo"
)

CHANNEL = os.getenv(
    "ANNOUNCE_CHANNEL",
    "@meow_lottery"
)

DB = os.getenv(
    "DATABASE_PATH",
    "lottery.db"
)

# هر 30 ثانیه پایان قرعه‌کشی بررسی می‌شود
DRAW_CHECK_INTERVAL = 30

# پیام کانال هر یک ساعت آپدیت می‌شود
CHANNEL_UPDATE_INTERVAL = 3600


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "MEOW LOTTERY BOT IS RUNNING."


def run_web_server():
    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )


# ============================================================
# DATABASE LOCK
# ============================================================

DB_LOCK = Lock()


# ============================================================
# DATABASE
# ============================================================

def get_db():
    con = sqlite3.connect(
        DB,
        timeout=30,
        check_same_thread=False
    )

    con.row_factory = sqlite3.Row

    return con


def setup_database():

    with DB_LOCK:

        con = get_db()

        # ----------------------------------------------------
        # USERS
        # ----------------------------------------------------

        con.execute("""
            CREATE TABLE IF NOT EXISTS users (

                user_id INTEGER PRIMARY KEY,

                username TEXT DEFAULT '',

                first_name TEXT DEFAULT '',

                welcome_seen INTEGER DEFAULT 0,

                last_seen TEXT DEFAULT NULL

            )
        """)

        # ----------------------------------------------------
        # LOTTERIES
        # ----------------------------------------------------

        con.execute("""
            CREATE TABLE IF NOT EXISTS lotteries (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                title TEXT NOT NULL,

                winners INTEGER NOT NULL,

                end_time TEXT NOT NULL,

                channels TEXT DEFAULT '',

                status TEXT DEFAULT 'active',

                channel_message_id INTEGER DEFAULT NULL,

                created_at TEXT DEFAULT NULL,

                last_channel_update TEXT DEFAULT NULL

            )
        """)

        # ----------------------------------------------------
        # PARTICIPANTS
        # ----------------------------------------------------

        con.execute("""
            CREATE TABLE IF NOT EXISTS participants (

                lottery_id INTEGER NOT NULL,

                user_id INTEGER NOT NULL,

                username TEXT DEFAULT '',

                first_name TEXT DEFAULT '',

                joined_at TEXT DEFAULT NULL,

                UNIQUE (
                    lottery_id,
                    user_id
                )

            )
        """)

        con.commit()
        con.close()


# ============================================================
# DATABASE MIGRATION
# ============================================================

def migrate_database():

    with DB_LOCK:

        con = get_db()

        # ----------------------------------------------------
        # USERS
        # ----------------------------------------------------

        columns = con.execute(
            "PRAGMA table_info(users)"
        ).fetchall()

        names = {
            row["name"]
            for row in columns
        }

        if "welcome_seen" not in names:

            con.execute("""
                ALTER TABLE users
                ADD COLUMN welcome_seen
                INTEGER DEFAULT 0
            """)

        if "last_seen" not in names:

            con.execute("""
                ALTER TABLE users
                ADD COLUMN last_seen
                TEXT DEFAULT NULL
            """)

        # ----------------------------------------------------
        # LOTTERIES
        # ----------------------------------------------------

        columns = con.execute(
            "PRAGMA table_info(lotteries)"
        ).fetchall()

        names = {
            row["name"]
            for row in columns
        }

        if "channel_message_id" not in names:

            con.execute("""
                ALTER TABLE lotteries
                ADD COLUMN channel_message_id
                INTEGER DEFAULT NULL
            """)

        if "created_at" not in names:

            con.execute("""
                ALTER TABLE lotteries
                ADD COLUMN created_at
                TEXT DEFAULT NULL
            """)

        if "last_channel_update" not in names:

            con.execute("""
                ALTER TABLE lotteries
                ADD COLUMN last_channel_update
                TEXT DEFAULT NULL
            """)

        # ----------------------------------------------------
        # PARTICIPANTS
        # ----------------------------------------------------

        columns = con.execute(
            "PRAGMA table_info(participants)"
        ).fetchall()

        names = {
            row["name"]
            for row in columns
        }

        if "joined_at" not in names:

            con.execute("""
                ALTER TABLE participants
                ADD COLUMN joined_at
                TEXT DEFAULT NULL
            """)

        con.commit()
        con.close()


# ============================================================
# TIME
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def iso_now():
    return now_utc().isoformat()


def seconds_remaining(end_time):

    try:

        end = datetime.fromisoformat(
            end_time
        )

        return int(
            (
                end - now_utc()
            ).total_seconds()
        )

    except Exception:

        return 0


def format_remaining(end_time):

    seconds = seconds_remaining(
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

    if days > 0:

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


# ============================================================
# GENERAL HELPERS
# ============================================================

def is_admin(user_id):
    return user_id == ADMIN_ID


def clean_channel(value):

    return (
        value
        .strip()
        .replace(
            "https://t.me/",
            ""
        )
        .replace(
            "@",
            ""
        )
    )


def main_channel_url():

    username = clean_channel(
        CHANNEL
    )

    return (
        f"https://t.me/{username}"
    )


# ============================================================
# USER DATABASE
# ============================================================

def save_user(user):

    with DB_LOCK:

        con = get_db()

        existing = con.execute(
            """
            SELECT user_id
            FROM users
            WHERE user_id=?
            """,
            (user.id,)
        ).fetchone()

        if existing:

            con.execute(
                """
                UPDATE users

                SET username=?,
                    first_name=?,
                    last_seen=?

                WHERE user_id=?
                """,
                (
                    user.username or "",
                    user.first_name or "",
                    iso_now(),
                    user.id
                )
            )

        else:

            con.execute(
                """
                INSERT INTO users
                (
                    user_id,
                    username,
                    first_name,
                    welcome_seen,
                    last_seen
                )

                VALUES (?, ?, ?, 0, ?)
                """,
                (
                    user.id,
                    user.username or "",
                    user.first_name or "",
                    iso_now()
                )
            )

        con.commit()
        con.close()


def get_user(user_id):

    con = get_db()

    row = con.execute(
        """
        SELECT *
        FROM users
        WHERE user_id=?
        """,
        (user_id,)
    ).fetchone()

    con.close()

    return row


def set_welcome_seen(
    user_id,
    value
):

    with DB_LOCK:

        con = get_db()

        con.execute(
            """
            UPDATE users

            SET welcome_seen=?

            WHERE user_id=?
            """,
            (
                value,
                user_id
            )
        )

        con.commit()
        con.close()


# ============================================================
# CHANNEL MEMBERSHIP
# ============================================================

async def check_channel_membership(
    context,
    user_id,
    channel=None
):

    target = channel or CHANNEL

    try:

        member = await context.bot.get_chat_member(
            chat_id=target,
            user_id=user_id
        )

        return member.status in (
            "member",
            "administrator",
            "creator"
        )

    except Exception as error:

        print(
            "MEMBERSHIP ERROR:",
            error
        )

        return False


# ============================================================
# LOTTERY DATABASE
# ============================================================

def get_lottery(lottery_id):

    con = get_db()

    row = con.execute(
        """
        SELECT *
        FROM lotteries
        WHERE id=?
        """,
        (lottery_id,)
    ).fetchone()

    con.close()

    return row


def get_active_lotteries():

    con = get_db()

    rows = con.execute(
        """
        SELECT *
        FROM lotteries

        WHERE status='active'

        ORDER BY id DESC
        """
    ).fetchall()

    con.close()

    return rows


def count_participants(
    lottery_id
):

    con = get_db()

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


def get_participants(
    lottery_id
):

    con = get_db()

    rows = con.execute(
        """
        SELECT *

        FROM participants

        WHERE lottery_id=?

        ORDER BY rowid ASC
        """,
        (lottery_id,)
    ).fetchall()

    con.close()

    return rows


# ============================================================
# LOTTERY MESSAGE
# ============================================================

def build_lottery_message(
    lottery,
    bot_username
):

    count = count_participants(
        lottery["id"]
    )

    bot_username = (
        bot_username
        or "ربات"
    )

    start_link = (
        f"https://t.me/"
        f"{bot_username}"
        f"?start=lottery_"
        f"{lottery['id']}"
    )

    return (

        f"🎁 <b>{lottery['title']}</b>\n\n"

        f"🏆 تعداد برنده‌ها: "
        f"<b>{lottery['winners']}</b> نفر\n"

        f"👥 شرکت‌کنندگان: "
        f"<b>{count}</b> نفر\n"

        f"⏱ زمان باقی‌مانده: "
        f"<b>{format_remaining(lottery['end_time'])}</b>\n\n"

        "🤖 <b>نحوه شرکت</b>\n"
        "برای شرکت، ابتدا ربات را Start کنید:\n"

        f"@{bot_username}\n\n"

        f"🔗 لینک مستقیم:\n"
        f"{start_link}\n\n"

        "👇 سپس روی دکمه زیر بزنید."
    )


def lottery_keyboard(
    lottery_id
):

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "🎰 شرکت در قرعه‌کشی",
                callback_data=(
                    f"join:{lottery_id}"
                )
            )
        ]

    ])


# ============================================================
# MEMBERSHIP KEYBOARD
# ============================================================

def membership_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "📢 عضویت در کانال",
                url=main_channel_url()
            )
        ],

        [
            InlineKeyboardButton(
                "✅ بررسی عضویت",
                callback_data=(
                    "check_membership"
                )
            )
        ]

    ])


# ============================================================
# ADMIN KEYBOARD
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
                "🔄 بروزرسانی پنل",
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

    user = update.effective_user

    save_user(user)

    # --------------------------------------------------------
    # DEEP LINK
    # --------------------------------------------------------

    if context.args:

        argument = context.args[0]

        if argument.startswith(
            "lottery_"
        ):

            try:

                lottery_id = int(
                    argument.replace(
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
                    "⛔ این قرعه‌کشی دیگر فعال نیست."
                )

                return

            if seconds_remaining(
                lottery["end_time"]
            ) <= 0:

                await update.message.reply_text(
                    "⛔ زمان این قرعه‌کشی تمام شده است."
                )

                return

            member = await check_channel_membership(
                context,
                user.id
            )

            if not member:

                set_welcome_seen(
                    user.id,
                    0
                )

                await update.message.reply_text(

                    "❌ برای شرکت در قرعه‌کشی "
                    "ابتدا باید عضو کانال شوید.\n\n"

                    f"📢 کانال:\n"
                    f"{main_channel_url()}",

                    reply_markup=(
                        membership_keyboard()
                    )
                )

                return

            bot = await context.bot.get_me()

            await update.message.reply_text(

                build_lottery_message(
                    lottery,
                    bot.username
                ),

                parse_mode="HTML",

                reply_markup=(
                    lottery_keyboard(
                        lottery_id
                    )
                )
            )

            return

    # --------------------------------------------------------
    # ADMIN START
    # --------------------------------------------------------

    if is_admin(user.id):

        await update.message.reply_text(

            "⚙️ <b>پنل مدیریت لاتاری میویی</b>\n\n"
            "یکی از گزینه‌ها را انتخاب کن:",

            parse_mode="HTML",

            reply_markup=admin_keyboard()
        )

        return

    # --------------------------------------------------------
    # NORMAL USER
    # --------------------------------------------------------

    user_record = get_user(
        user.id
    )

    member = await check_channel_membership(
        context,
        user.id
    )

    if user_record and user_record["welcome_seen"] == 1:

        if member:

            # قبلاً تأیید شده
            # هیچ پیام تکراری ارسال نمی‌شود

            return

        # لفت داده
        set_welcome_seen(
            user.id,
            0
        )

    if not member:

        await update.message.reply_text(

            "🎰 <b>به لاتاری میویی خوش اومدید!</b>\n\n"

            "برای استفاده از ربات باید "
            "ابتدا در کانال زیر عضو شوید.\n\n"

            f"📢 کانال:\n"
            f"{main_channel_url()}\n\n"

            "بعد از عضویت روی "
            "«بررسی عضویت» بزنید.",

            parse_mode="HTML",

            reply_markup=(
                membership_keyboard()
            )
        )

        return

    set_welcome_seen(
        user.id,
        1
    )

    await update.message.reply_text(

        "🎉 <b>به لاتاری میویی خوش اومدید!</b>\n\n"

        "✅ عضویت شما تأیید شد.\n\n"

        "برای شرکت در قرعه‌کشی‌ها "
        "از لینک‌های منتشرشده در کانال استفاده کنید.",

        parse_mode="HTML"
    )


# ============================================================
# CHECK MEMBERSHIP
# ============================================================

async def check_membership(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    user = query.from_user

    member = await check_channel_membership(
        context,
        user.id
    )

    if not member:

        await query.answer(

            "❌ هنوز عضو کانال نشده‌اید.",

            show_alert=True
        )

        return

    save_user(user)

    set_welcome_seen(
        user.id,
        1
    )

    await query.answer(
        "✅ عضویت تأیید شد."
    )

    try:

        await query.edit_message_text(

            "🎉 <b>عضویت شما تأیید شد!</b>\n\n"

            "حالا می‌توانید در "
            "قرعه‌کشی‌های میویی شرکت کنید. 🍀",

            parse_mode="HTML"
        )

    except Exception:
        pass


# ============================================================
# JOIN LOTTERY
# ============================================================

async def join_lottery(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    user = query.from_user

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

            "⛔ این قرعه‌کشی پایان یافته.",

            show_alert=True
        )

        return

    if seconds_remaining(
        lottery["end_time"]
    ) <= 0:

        await query.answer(

            "⛔ زمان قرعه‌کشی تمام شده.",

            show_alert=True
        )

        return

    # --------------------------------------------------------
    # MAIN CHANNEL
    # --------------------------------------------------------

    member = await check_channel_membership(
        context,
        user.id
    )

    if not member:

        set_welcome_seen(
            user.id,
            0
        )

        await query.answer(

            "❌ ابتدا عضو کانال شوید.",

            show_alert=True
        )

        try:

            await context.bot.send_message(

                chat_id=user.id,

                text=(

                    "📢 برای شرکت در قرعه‌کشی "
                    "ابتدا باید عضو کانال شوید.\n\n"

                    f"{main_channel_url()}"
                ),

                reply_markup=(
                    membership_keyboard()
                )
            )

        except Exception:
            pass

        return

    # --------------------------------------------------------
    # REQUIRED CHANNELS
    # --------------------------------------------------------

    channels = (
        lottery["channels"]
        or ""
    )

    missing = []

    for raw_channel in channels.split(","):

        raw_channel = raw_channel.strip()

        if not raw_channel:
            continue

        target = raw_channel

        try:

            member = await context.bot.get_chat_member(

                chat_id=target,

                user_id=user.id
            )

            if member.status in (
                "left",
                "kicked"
            ):

                missing.append(
                    clean_channel(
                        target
                    )
                )

        except Exception:

            missing.append(
                clean_channel(
                    target
                )
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

            "❌ ابتدا عضو کانال‌های لازم شوید.",

            show_alert=True
        )

        try:

            await context.bot.send_message(

                chat_id=user.id,

                text=(
                    "📢 برای شرکت در این "
                    "قرعه‌کشی باید در کانال‌های "
                    "لازم عضو باشید:"
                ),

                reply_markup=(
                    InlineKeyboardMarkup(
                        buttons
                    )
                )
            )

        except Exception:
            pass

        return

    # --------------------------------------------------------
    # REGISTER
    # --------------------------------------------------------

    save_user(user)

    with DB_LOCK:

        con = get_db()

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
                    iso_now()
                )
            )

            con.commit()

            registered = True

        except sqlite3.IntegrityError:

            registered = False

        finally:

            con.close()

    if not registered:

        await query.answer(

            "ℹ️ شما قبلاً شرکت کرده‌اید.",

            show_alert=True
        )

        return

    # --------------------------------------------------------
    # SUCCESS
    # --------------------------------------------------------

    await query.answer(

        "🎉 با موفقیت شرکت کردید!",

        show_alert=True
    )

    try:

        await context.bot.send_message(

            chat_id=user.id,

            text=(

                "🎉 <b>ثبت شد!</b>\n\n"

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

    context.user_data[
        "step"
    ] = "title"

    await update.message.reply_text(

        "🎁 <b>قرعه‌کشی جدید</b>\n\n"
        "نام یا عنوان جایزه را ارسال کن:",

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
            "مثال: 3"
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

                "❌ تعداد برنده باید یک "
                "عدد صحیح مثبت باشد."
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
            "مثال: 24"
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

                "❌ مدت زمان نامعتبر است."
            )

            return

        context.user_data[
            "hours"
        ] = hours

        context.user_data[
            "step"
        ] = "channels"

        await update.message.reply_text(

            "📢 کانال‌های عضویت اجباری را ارسال کن.\n\n"

            "مثال:\n"
            "@channel1,@channel2\n\n"

            "اگر لازم نیست:\n"
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

        created = now_utc()

        end = (
            created
            + timedelta(
                hours=hours
            )
        )

        with DB_LOCK:

            con = get_db()

            cursor = con.execute(

                """
                INSERT INTO lotteries
                (
                    title,
                    winners,
                    end_time,
                    channels,
                    status,
                    created_at,
                    last_channel_update
                )

                VALUES (
                    ?, ?, ?, ?, 'active', ?, ?
                )
                """,

                (
                    title,
                    winners,
                    end.isoformat(),
                    channels,
                    created.isoformat(),
                    created.isoformat()
                )
            )

            lottery_id = cursor.lastrowid

            con.commit()
            con.close()

        lottery = get_lottery(
            lottery_id
        )

        try:

            bot = await context.bot.get_me()

            sent = await context.bot.send_message(

                chat_id=CHANNEL,

                text=build_lottery_message(
                    lottery,
                    bot.username
                ),

                parse_mode="HTML",

                reply_markup=(
                    lottery_keyboard(
                        lottery_id
                    )
                )
            )

            with DB_LOCK:

                con = get_db()

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

                "✅ <b>قرعه‌کشی ساخته شد.</b>\n\n"

                f"🆔 شناسه: "
                f"<code>{lottery_id}</code>\n"

                f"🎁 جایزه: "
                f"{title}\n"

                f"🏆 برنده‌ها: "
                f"{winners}\n"

                f"⏱ مدت: "
                f"{hours} ساعت",

                parse_mode="HTML"
            )

        except Exception as error:

            await update.message.reply_text(

                "❌ ارسال قرعه‌کشی به کانال ناموفق بود:\n\n"

                f"<code>{error}</code>",

                parse_mode="HTML"
            )

        context.user_data.clear()


# ============================================================
# DRAW
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

    # --------------------------------------------------------
    # ATOMIC LOCK
    # --------------------------------------------------------

    with DB_LOCK:

        con = get_db()

        changed = con.execute(

            """
            UPDATE lotteries

            SET status='drawing'

            WHERE id=?
              AND status='active'
            """,

            (lottery_id,)
        )

        con.commit()

        con.close()

    if changed.rowcount != 1:

        # یک پروسه دیگر قرعه‌کشی را گرفته
        return False

    # --------------------------------------------------------
    # PARTICIPANTS
    # --------------------------------------------------------

    users = get_participants(
        lottery_id
    )

    # --------------------------------------------------------
    # NO PARTICIPANTS
    # --------------------------------------------------------

    if not users:

        with DB_LOCK:

            con = get_db()

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

            "❌ هیچ شرکت‌کننده‌ای وجود نداشت."
        )

        try:

            await context.bot.send_message(

                chat_id=CHANNEL,

                text=result,

                parse_mode="HTML"
            )

        except Exception as error:

            print(
                "NO PARTICIPANT RESULT ERROR:",
                error
            )

        return True

    # --------------------------------------------------------
    # WINNERS
    # --------------------------------------------------------

    winner_count = min(

        int(lottery["winners"]),

        len(users)
    )

    winners = random.sample(
        users,
        winner_count
    )

    # --------------------------------------------------------
    # MARK DRAWN
    # --------------------------------------------------------

    with DB_LOCK:

        con = get_db()

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

        username = (

            "@"
            + winner["username"]

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

    except Exception as error:

        print(
            "CHANNEL RESULT ERROR:",
            error
        )

    # --------------------------------------------------------
    # WINNER PRIVATE MESSAGE
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

                    "📩 برای دریافت جایزه "
                    "به ادمین پیام دهید:\n"

                    f"<b>{ADMIN_USERNAME}</b>\n\n"

                    "🍀 مبارکتان باشد!"
                ),

                parse_mode="HTML"
            )

        except Exception as error:

            print(
                "WINNER MESSAGE ERROR:",
                error
            )

    return True


# ============================================================
# DRAW COMMAND
# ============================================================

async def draw_command(
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

    if seconds_remaining(
        lottery["end_time"]
    ) > 0:

        await update.message.reply_text(

            "⚠️ زمان قرعه‌کشی هنوز تمام نشده.\n\n"
            "اگر می‌خواهی با این حال اجرا شود، "
            "در نسخه بعدی می‌توانیم گزینه تأیید "
            "برای قرعه‌کشی دستی قبل از پایان زمان "
            "اضافه کنیم."
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
# LIST COMMAND
# ============================================================

async def list_command(
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

    text = (
        "📋 <b>قرعه‌کشی‌های فعال</b>\n\n"
    )

    for lottery in lotteries:

        text += (

            f"🆔 <b>{lottery['id']}</b>\n"

            f"🎁 {lottery['title']}\n"

            f"🏆 برنده‌ها: "
            f"{lottery['winners']}\n"

            f"👥 شرکت‌کنندگان: "
            f"{count_participants(lottery['id'])}\n"

            f"⏱ باقی‌مانده: "
            f"{format_remaining(lottery['end_time'])}\n\n"
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

            "مثال:\n"
            "/cancel 1"
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

        "⚠️ <b>تأیید لغو</b>\n\n"

        f"🎁 {lottery['title']}\n\n"

        "آیا مطمئنی که می‌خواهی "
        "این قرعه‌کشی لغو شود؟",

        parse_mode="HTML",

        reply_markup=keyboard
    )


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
            "⛔ فعال نیست.",
            show_alert=True
        )

        return

    with DB_LOCK:

        con = get_db()

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

                    "❌ <b>قرعه‌کشی لغو شد</b>\n\n"

                    f"🎁 جایزه:\n"
                    f"<b>{lottery['title']}</b>\n\n"

                    "⛔ این قرعه‌کشی دیگر "
                    "قابل شرکت نیست."
                ),

                parse_mode="HTML",

                reply_markup=None
            )

        except Exception as error:

            print(
                "CANCEL EDIT ERROR:",
                error
            )

    await query.answer(
        "✅ قرعه‌کشی لغو شد."
    )

    await query.edit_message_text(

        "✅ <b>قرعه‌کشی لغو شد.</b>",

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
        "عملیات لغو شد."
    )

    await query.edit_message_text(
        "❌ عملیات لغو شد."
    )


# ============================================================
# ADMIN PANEL
# ============================================================

async def admin_command(
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
            "یکی از گزینه‌ها را انتخاب کن:",

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
                f"👥 {count_participants(lottery['id'])}\n"
                f"⏱ {format_remaining(lottery['end_time'])}\n\n"
            )

        await query.message.reply_text(

            text,

            parse_mode="HTML"
        )

        return

    # --------------------------------------------------------
    # PARTICIPANTS
    # --------------------------------------------------------

    if action == "admin:participants":

        await query.message.reply_text(

            "👥 برای مشاهده شرکت‌کنندگان:\n\n"
            "/participants ID\n\n"
            "مثال:\n"
            "/participants 1"
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
# CHANNEL UPDATE JOB
# ============================================================

async def update_channel_messages(
    context: ContextTypes.DEFAULT_TYPE
):

    lotteries = get_active_lotteries()

    if not lotteries:
        return

    try:

        bot = await context.bot.get_me()

    except Exception:
        return

    for lottery in lotteries:

        if seconds_remaining(
            lottery["end_time"]
        ) <= 0:

            continue

        message_id = (
            lottery["channel_message_id"]
        )

        if not message_id:
            continue

        try:

            # مهم:
            # متن + دکمه با هم آپدیت می‌شوند
            # بنابراین دکمه هرگز حذف نمی‌شود.

            await context.bot.edit_message_text(

                chat_id=CHANNEL,

                message_id=message_id,

                text=build_lottery_message(
                    lottery,
                    bot.username
                ),

                parse_mode="HTML",

                reply_markup=(
                    lottery_keyboard(
                        lottery["id"]
                    )
                )
            )

            with DB_LOCK:

                con = get_db()

                con.execute(

                    """
                    UPDATE lotteries

                    SET last_channel_update=?

                    WHERE id=?
                    """,

                    (
                        iso_now(),
                        lottery["id"]
                    )
                )

                con.commit()
                con.close()

        except Exception as error:

            print(
                "CHANNEL UPDATE ERROR:",
                error
            )


# ============================================================
# AUTO DRAW JOB
# ============================================================

async def automatic_draw(
    context: ContextTypes.DEFAULT_TYPE
):

    lotteries = get_active_lotteries()

    for lottery in lotteries:

        if seconds_remaining(
            lottery["end_time"]
        ) > 0:

            continue

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


# ============================================================
# ERROR
# ============================================================

async def error_handler(
    update,
    context
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
        target=run_web_server,
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
            "admin",
            admin_command
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
            draw_command
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
            list_command
        )
    )

    application.add_handler(
        CommandHandler(
            "cancel",
            cancel_command
        )
    )

    # --------------------------------------------------------
    # CALLBACKS
    # --------------------------------------------------------

    application.add_handler(

        CallbackQueryHandler(

            check_membership,

            pattern=r"^check_membership$"
        )
    )

    application.add_handler(

        CallbackQueryHandler(

            join_lottery,

            pattern=r"^join:"
        )
    )

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
    # AUTOMATIC DRAW
    # --------------------------------------------------------

    application.job_queue.run_repeating(

        automatic_draw,

        interval=DRAW_CHECK_INTERVAL,

        first=10
    )

    # --------------------------------------------------------
    # CHANNEL TIMER
    # --------------------------------------------------------

    application.job_queue.run_repeating(

        update_channel_messages,

        interval=CHANNEL_UPDATE_INTERVAL,

        first=60
    )

    # --------------------------------------------------------
    # ERROR HANDLER
    # --------------------------------------------------------

    application.add_error_handler(
        error_handler
    )

    print(
        "================================"
    )

    print(
        "MEOW LOTTERY BOT STARTED"
    )

    print(
        "================================"
    )

    # --------------------------------------------------------
    # RUN
    # --------------------------------------------------------

    application.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()

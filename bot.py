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
from telegram.constants import ChatMemberStatus
from telegram.error import TelegramError, BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# =========================================================
# CONFIG
# =========================================================

TOKEN = os.getenv("BOT_TOKEN")

ADMIN_ID = int(
    os.getenv(
        "ADMIN_ID",
        "7111630140"
    )
)

CHANNEL = os.getenv(
    "ANNOUNCE_CHANNEL",
    "@meow_lottery"
)

ADMIN_USERNAME = os.getenv(
    "ADMIN_USERNAME",
    "@your_AmiRo"
)

DB_FILE = "lottery.db"

PORT = int(
    os.getenv(
        "PORT",
        "10000"
    )
)


# =========================================================
# WEB SERVER
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Meow Lottery Bot is running."


def run_web_server():

    app.run(
        host="0.0.0.0",
        port=PORT
    )


# =========================================================
# DATABASE
# =========================================================

def get_db():

    con = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    con.row_factory = sqlite3.Row

    return con


def setup_database():

    con = get_db()

    con.execute("""
        CREATE TABLE IF NOT EXISTS lotteries (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            title TEXT NOT NULL,

            winners INTEGER NOT NULL,

            end_time TEXT NOT NULL,

            channels TEXT DEFAULT '',

            status TEXT DEFAULT 'active',

            channel_message_id INTEGER,

            created_at TEXT NOT NULL,

            drawn_at TEXT

        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS participants (

            lottery_id INTEGER NOT NULL,

            user_id INTEGER NOT NULL,

            username TEXT DEFAULT '',

            first_name TEXT DEFAULT '',

            joined_at TEXT NOT NULL,

            UNIQUE(lottery_id, user_id)

        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS users (

            user_id INTEGER PRIMARY KEY,

            username TEXT DEFAULT '',

            first_name TEXT DEFAULT '',

            intro_seen INTEGER DEFAULT 0,

            updated_at TEXT NOT NULL

        )
    """)

    con.commit()

    con.close()


# =========================================================
# HELPERS
# =========================================================

def now_utc():

    return datetime.now(
        timezone.utc
    )


def is_admin(user_id):

    return user_id == ADMIN_ID


def get_lottery(lottery_id):

    con = get_db()

    lottery = con.execute(
        """
        SELECT *
        FROM lotteries
        WHERE id = ?
        """,
        (lottery_id,)
    ).fetchone()

    con.close()

    return lottery


def get_participants(lottery_id):

    con = get_db()

    users = con.execute(
        """
        SELECT *
        FROM participants
        WHERE lottery_id = ?
        ORDER BY joined_at ASC
        """,
        (lottery_id,)
    ).fetchall()

    con.close()

    return users


def participant_count(lottery_id):

    con = get_db()

    count = con.execute(
        """
        SELECT COUNT(*)
        FROM participants
        WHERE lottery_id = ?
        """,
        (lottery_id,)
    ).fetchone()[0]

    con.close()

    return count


def user_joined(
    lottery_id,
    user_id
):

    con = get_db()

    result = con.execute(
        """
        SELECT 1
        FROM participants
        WHERE lottery_id = ?
        AND user_id = ?
        """,
        (
            lottery_id,
            user_id
        )
    ).fetchone()

    con.close()

    return result is not None


def save_user(user):

    con = get_db()

    con.execute(
        """
        INSERT INTO users
        (
            user_id,
            username,
            first_name,
            updated_at
        )

        VALUES (?, ?, ?, ?)

        ON CONFLICT(user_id)

        DO UPDATE SET

            username = excluded.username,

            first_name = excluded.first_name,

            updated_at = excluded.updated_at
        """,
        (
            user.id,
            user.username or "",
            user.first_name or "",
            now_utc().isoformat()
        )
    )

    con.commit()

    con.close()


def intro_seen(user_id):

    con = get_db()

    row = con.execute(
        """
        SELECT intro_seen
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    ).fetchone()

    con.close()

    return bool(
        row and row["intro_seen"]
    )


def set_intro_seen(user_id):

    con = get_db()

    con.execute(
        """
        INSERT INTO users
        (
            user_id,
            intro_seen,
            updated_at
        )

        VALUES (?, 1, ?)

        ON CONFLICT(user_id)

        DO UPDATE SET

            intro_seen = 1,

            updated_at = excluded.updated_at
        """,
        (
            user_id,
            now_utc().isoformat()
        )
    )

    con.commit()

    con.close()


def parse_datetime(value):

    dt = datetime.fromisoformat(value)

    if dt.tzinfo is None:

        dt = dt.replace(
            tzinfo=timezone.utc
        )

    return dt


def remaining_time(end_time):

    seconds = int(
        (
            parse_datetime(end_time)
            - now_utc()
        ).total_seconds()
    )

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


def parse_channels(value):

    if not value:

        return []

    if value.lower() == "none":

        return []

    value = value.replace(
        "،",
        ","
    )

    return [
        x.strip()
        for x in value.split(",")
        if x.strip()
    ]


def clean_channel(channel):

    return channel.strip().lstrip("@")


# =========================================================
# LOTTERY MESSAGE
# =========================================================

def lottery_message(
    lottery,
    bot_username
):

    count = participant_count(
        lottery["id"]
    )

    bot_username = (
        bot_username
        or "YourBot"
    )

    if lottery["status"] == "active":

        timer = (
            f"⏱ زمان باقی‌مانده: "
            f"<b>{remaining_time(lottery['end_time'])}</b>"
        )

    else:

        timer = ""

    return (
        f"🎁 <b>{lottery['title']}</b>\n\n"

        f"🏆 تعداد برنده‌ها: "
        f"<b>{lottery['winners']}</b> نفر\n"

        f"👥 تعداد شرکت‌کنندگان: "
        f"<b>{count}</b> نفر\n"

        f"{timer}\n\n"

        "🤖 برای شرکت در قرعه‌کشی، "
        f"ابتدا ربات <b>@{bot_username.lstrip('@')}</b> "
        "را Start کنید.\n\n"

        "👇 سپس روی دکمه زیر بزنید."
    )


# =========================================================
# KEYBOARDS
# =========================================================

def lottery_keyboard(lottery_id):

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎰 شرکت در قرعه‌کشی",
                callback_data=f"join:{lottery_id}"
            )
        ]
    ])


def admin_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "🎁 قرعه‌کشی جدید",
                callback_data="admin:new"
            )
        ],

        [
            InlineKeyboardButton(
                "📋 قرعه‌کشی‌های فعال",
                callback_data="admin:list"
            ),

            InlineKeyboardButton(
                "👥 شرکت‌کنندگان",
                callback_data="admin:participants"
            )
        ],

        [
            InlineKeyboardButton(
                "🎲 قرعه‌کشی دستی",
                callback_data="admin:draw"
            ),

            InlineKeyboardButton(
                "❌ لغو قرعه‌کشی",
                callback_data="admin:cancel"
            )
        ],

        [
            InlineKeyboardButton(
                "ℹ️ راهنما",
                callback_data="admin:help"
            )
        ]

    ])


# =========================================================
# MEMBERSHIP
# =========================================================

async def check_membership(
    bot,
    user_id,
    channels
):

    missing = []

    for channel in channels:

        username = clean_channel(
            channel
        )

        try:

            member = await bot.get_chat_member(
                chat_id=f"@{username}",
                user_id=user_id
            )

            if member.status in (

                ChatMemberStatus.LEFT,

                ChatMemberStatus.KICKED

            ):

                missing.append(
                    username
                )

        except TelegramError:

            missing.append(
                username
            )

    return missing


def membership_keyboard(
    channels,
    callback
):

    buttons = []

    for channel in channels:

        username = clean_channel(
            channel
        )

        buttons.append([
            InlineKeyboardButton(
                f"📢 عضویت @{username}",
                url=f"https://t.me/{username}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔄 بررسی عضویت",
            callback_data=callback
        )
    ])

    return InlineKeyboardMarkup(
        buttons
    )


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:

        return

    user = update.effective_user

    save_user(user)

    # -----------------------------------------
    # Lottery deep link
    # -----------------------------------------

    if context.args:

        arg = context.args[0]

        if arg.startswith("lottery_"):

            try:

                lottery_id = int(
                    arg.split(
                        "_",
                        1
                    )[1]
                )

            except ValueError:

                await update.message.reply_text(
                    "❌ لینک نامعتبر است."
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

            await update.message.reply_text(
                lottery_message(
                    lottery,
                    context.bot.username
                ),
                parse_mode="HTML",
                reply_markup=lottery_keyboard(
                    lottery_id
                )
            )

            return

    # -----------------------------------------
    # ADMIN
    # -----------------------------------------

    if is_admin(user.id):

        await update.message.reply_text(

            "🛠 <b>پنل مدیریت لاتاری میویی</b>\n\n"
            "یکی از گزینه‌ها را انتخاب کنید:",

            parse_mode="HTML",

            reply_markup=admin_keyboard()

        )

        return

    # -----------------------------------------
    # INTRO
    # -----------------------------------------

    required_channels = parse_channels(
        CHANNEL
    )

    if not intro_seen(user.id):

        missing = await check_membership(

            context.bot,

            user.id,

            required_channels

        )

        if missing:

            await update.message.reply_text(

                "🐱 <b>به لاتاری میویی خوش آمدید!</b>\n\n"

                "برای اجرای ربات باید ابتدا "
                "در کانال زیر عضو شوید.\n\n"

                "بعد از عضویت روی "
                "«بررسی عضویت» بزنید.",

                parse_mode="HTML",

                reply_markup=membership_keyboard(
                    missing,
                    "check_intro"
                )

            )

            return

        set_intro_seen(
            user.id
        )

    await update.message.reply_text(

        "🐱 <b>به لاتاری میویی خوش آمدید!</b>\n\n"
        "حالا می‌توانید در قرعه‌کشی‌ها شرکت کنید.",

        parse_mode="HTML"
    )


# =========================================================
# CHECK INTRO
# =========================================================

async def check_intro(
    update,
    context
):

    query = update.callback_query

    user = query.from_user

    channels = parse_channels(
        CHANNEL
    )

    missing = await check_membership(

        context.bot,

        user.id,

        channels
    )

    if missing:

        await query.answer(

            "❌ هنوز عضو کانال نیستید.",

            show_alert=True
        )

        return

    set_intro_seen(
        user.id
    )

    await query.answer(
        "✅ عضویت تأیید شد!",
        show_alert=True
    )

    try:

        await query.edit_message_text(

            "✅ عضویت شما تأیید شد.\n\n"
            "🐱 حالا می‌توانید در "
            "قرعه‌کشی‌ها شرکت کنید."

        )

    except BadRequest:

        pass


# =========================================================
# JOIN
# =========================================================

async def join_lottery(
    update,
    context
):

    query = update.callback_query

    try:

        lottery_id = int(
            query.data.split(":")[1]
        )

    except:

        await query.answer(
            "❌ درخواست نامعتبر.",
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
            "⛔ این قرعه‌کشی فعال نیست.",
            show_alert=True
        )

        return

    if now_utc() >= parse_datetime(
        lottery["end_time"]
    ):

        await query.answer(
            "⛔ زمان قرعه‌کشی تمام شده.",
            show_alert=True
        )

        return

    channels = parse_channels(
        lottery["channels"]
    )

    if channels:

        missing = await check_membership(

            context.bot,

            query.from_user.id,

            channels
        )

        if missing:

            await query.answer(

                "❌ ابتدا عضو کانال شوید.",

                show_alert=True
            )

            await query.message.reply_text(

                "📢 ابتدا در کانال‌های زیر عضو شوید:",

                reply_markup=membership_keyboard(

                    missing,

                    f"recheck:{lottery_id}"

                )
            )

            return

    if user_joined(

        lottery_id,

        query.from_user.id

    ):

        await query.answer(

            "ℹ️ شما قبلاً شرکت کرده‌اید.",

            show_alert=True
        )

        return

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

                query.from_user.id,

                query.from_user.username or "",

                query.from_user.first_name or "",

                now_utc().isoformat()
            )
        )

        con.commit()

    except sqlite3.IntegrityError:

        pass

    finally:

        con.close()

    await query.answer(

        "🎉 شما با موفقیت شرکت کردید!",

        show_alert=True
    )

    # پیام PV
    try:

        await context.bot.send_message(

            chat_id=query.from_user.id,

            text=(

                "🎉 <b>ثبت شد!</b>\n\n"

                f"شما در قرعه‌کشی "
                f"<b>{lottery['title']}</b> "
                "شرکت کردید.\n\n"

                "🍀 موفق باشید!"

            ),

            parse_mode="HTML"

        )

    except TelegramError:

        pass


# =========================================================
# RECHECK LOTTERY
# =========================================================

async def recheck_lottery(
    update,
    context
):

    query = update.callback_query

    lottery_id = int(
        query.data.split(":")[1]
    )

    lottery = get_lottery(
        lottery_id
    )

    if not lottery:

        await query.answer(
            "❌ قرعه‌کشی پیدا نشد.",
            show_alert=True
        )

        return

    if user_joined(
        lottery_id,
        query.from_user.id
    ):

        await query.answer(
            "ℹ️ شما قبلاً شرکت کرده‌اید.",
            show_alert=True
        )

        return

    missing = await check_membership(

        context.bot,

        query.from_user.id,

        parse_channels(
            lottery["channels"]
        )

    )

    if missing:

        await query.answer(

            "❌ هنوز عضویت شما تأیید نشده.",

            show_alert=True
        )

        return

    con = get_db()

    con.execute(

        """
        INSERT OR IGNORE INTO participants
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

            now_utc().isoformat()
        )
    )

    con.commit()

    con.close()

    await query.answer(

        "🎉 عضویت تأیید شد و شرکت کردید!",

        show_alert=True
    )


# =========================================================
# NEW LOTTERY
# =========================================================

async def new_lottery(
    update,
    context
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

        "🎁 <b>ساخت قرعه‌کشی</b>\n\n"
        "عنوان جایزه را بفرست:",

        parse_mode="HTML"
    )


# =========================================================
# ADMIN INPUT
# =========================================================

async def admin_input(
    update,
    context
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

    value = update.message.text.strip()

    # TITLE
    if step == "title":

        context.user_data[
            "title"
        ] = value

        context.user_data[
            "step"
        ] = "winners"

        await update.message.reply_text(

            "🏆 تعداد برنده‌ها را بفرست:\n\n"
            "مثال: <code>3</code>",

            parse_mode="HTML"
        )

        return

    # WINNERS
    if step == "winners":

        try:

            winners = int(value)

            if winners < 1:

                raise ValueError

        except:

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

            "⏱ چند ساعت فعال باشد؟\n\n"
            "مثال: <code>24</code>",

            parse_mode="HTML"
        )

        return

    # DURATION
    if step == "duration":

        try:

            hours = float(value)

            if hours <= 0:

                raise ValueError

        except:

            await update.message.reply_text(

                "❌ زمان نامعتبر است."

            )

            return

        context.user_data[
            "hours"
        ] = hours

        context.user_data[
            "step"
        ] = "channels"

        await update.message.reply_text(

            "📢 کانال عضویت اجباری را وارد کن.\n\n"

            "مثال:\n"
            "<code>@channel</code>\n\n"

            "بدون کانال:\n"
            "<code>none</code>",

            parse_mode="HTML"
        )

        return

    # CHANNELS
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
                created_at
            )

            VALUES (?, ?, ?, ?, 'active', ?)
            """,

            (
                title,

                winners,

                end_time,

                channels,

                now_utc().isoformat()
            )
        )

        lottery_id = cursor.lastrowid

        con.commit()

        con.close()

        lottery = get_lottery(
            lottery_id
        )

        try:

            sent = await context.bot.send_message(

                chat_id=CHANNEL,

                text=lottery_message(

                    lottery,

                    context.bot.username

                ),

                parse_mode="HTML",

                reply_markup=lottery_keyboard(

                    lottery_id

                )

            )

            con = get_db()

            con.execute(

                """
                UPDATE lotteries

                SET channel_message_id = ?

                WHERE id = ?
                """,

                (
                    sent.message_id,

                    lottery_id
                )
            )

            con.commit()

            con.close()

            await update.message.reply_text(

                "✅ قرعه‌کشی ساخته شد و "
                "در کانال منتشر شد.",

                reply_markup=admin_keyboard()

            )

        except TelegramError as error:

            await update.message.reply_text(

                f"❌ خطا در ارسال به کانال:\n{error}"

            )

        context.user_data.clear()


# =========================================================
# DRAW
# =========================================================

async def perform_draw(
    bot,
    lottery_id
):

    lottery = get_lottery(
        lottery_id
    )

    if not lottery:

        return (
            False,
            "❌ قرعه‌کشی پیدا نشد.",
            []
        )

    if lottery["status"] != "active":

        return (
            False,
            "⛔ این قرعه‌کشی قبلاً انجام شده.",
            []
        )

    users = get_participants(
        lottery_id
    )

    if not users:

        return (
            False,
            "❌ هیچ شرکت‌کننده‌ای وجود ندارد.",
            []
        )

    # تعداد برنده هرگز بیشتر از شرکت‌کنندگان نیست.
    winner_count = min(

        int(lottery["winners"]),

        len(users)

    )

    winners = random.sample(

        users,

        winner_count

    )

    # قفل کردن قرعه‌کشی
    con = get_db()

    con.execute(

        """
        UPDATE lotteries

        SET
            status = 'drawn',
            drawn_at = ?

        WHERE id = ?

        AND status = 'active'
        """,

        (
            now_utc().isoformat(),

            lottery_id
        )
    )

    con.commit()

    con.close()

    # نتیجه
    result = (

        "🎊 <b>نتیجه قرعه‌کشی</b>\n\n"

        f"🎁 جایزه: "
        f"<b>{lottery['title']}</b>\n\n"

    )

    for index, winner in enumerate(
        winners,
        1
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

    channel_result = (

        "🎉 <b>قرعه‌کشی به پایان رسید!</b>\n\n"

        + result

        + "❤️ ممنون از شرکت شما"

    )

    # ویرایش همان پیام کانال
    if lottery["channel_message_id"]:

        try:

            await bot.edit_message_text(

                chat_id=CHANNEL,

                message_id=lottery[
                    "channel_message_id"
                ],

                text=channel_result,

                parse_mode="HTML",

                reply_markup=None

            )

        except TelegramError:

            await bot.send_message(

                chat_id=CHANNEL,

                text=channel_result,

                parse_mode="HTML"

            )

    else:

        await bot.send_message(

            chat_id=CHANNEL,

            text=channel_result,

            parse_mode="HTML"

        )

    # پیام خصوصی برنده‌ها
    for winner in winners:

        try:

            await bot.send_message(

                chat_id=winner[
                    "user_id"
                ],

                text=(

                    "🎉 <b>تبریک!</b>\n\n"

                    "🏆 شما برنده قرعه‌کشی شدید!\n\n"

                    f"🎁 جایزه: "
                    f"<b>{lottery['title']}</b>\n\n"

                    "📩 برای دریافت جایزه "
                    "به ادمین پیام دهید:\n\n"

                    f"<b>{ADMIN_USERNAME}</b>"

                ),

                parse_mode="HTML"

            )

        except TelegramError:

            pass

    return (
        True,
        result,
        winners
    )


# =========================================================
# MANUAL DRAW
# =========================================================

async def draw_command(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):

        return

    if not context.args:

        await update.message.reply_text(

            "مثال:\n"
            "<code>/draw 1</code>",

            parse_mode="HTML"

        )

        return

    try:

        lottery_id = int(
            context.args[0]
        )

    except:

        await update.message.reply_text(

            "❌ شناسه نامعتبر است."

        )

        return

    ok, result, winners = await perform_draw(

        context.bot,

        lottery_id

    )

    if ok:

        await update.message.reply_text(

            "✅ قرعه‌کشی با موفقیت انجام شد.\n\n"
            + result,

            parse_mode="HTML"

        )

    else:

        await update.message.reply_text(
            result
        )


# =========================================================
# AUTO DRAW
# =========================================================

async def auto_draw_job(
    context
):

    con = get_db()

    lotteries = con.execute(

        """
        SELECT id

        FROM lotteries

        WHERE status = 'active'

        AND end_time <= ?
        """,

        (
            now_utc().isoformat(),
        )

    ).fetchall()

    con.close()

    for lottery in lotteries:

        try:

            await perform_draw(

                context.bot,

                lottery["id"]

            )

        except Exception as error:

            print(
                "AUTO DRAW ERROR:",
                error
            )


# =========================================================
# UPDATE CHANNEL EVERY MINUTE
# =========================================================

async def update_channel_job(
    context
):

    con = get_db()

    lotteries = con.execute(

        """
        SELECT *

        FROM lotteries

        WHERE status = 'active'

        AND channel_message_id IS NOT NULL
        """
    ).fetchall()

    con.close()

    for lottery in lotteries:

        # اگر زمان تمام شده، قرعه‌کشی انجام شود.
        if now_utc() >= parse_datetime(
            lottery["end_time"]
        ):

            try:

                await perform_draw(

                    context.bot,

                    lottery["id"]

                )

            except Exception as error:

                print(
                    "DRAW ERROR:",
                    error
                )

            continue

        try:

            await context.bot.edit_message_text(

                chat_id=CHANNEL,

                message_id=lottery[
                    "channel_message_id"
                ],

                text=lottery_message(

                    lottery,

                    context.bot.username

                ),

                parse_mode="HTML",

                # دکمه را حفظ می‌کنیم.
                reply_markup=lottery_keyboard(

                    lottery["id"]

                )

            )

        except BadRequest as error:

            if (
                "not modified"
                not in str(error).lower()
            ):

                print(
                    "CHANNEL EDIT ERROR:",
                    error
                )

        except TelegramError as error:

            print(
                "CHANNEL UPDATE ERROR:",
                error
            )


# =========================================================
# PARTICIPANTS COMMAND
# =========================================================

async def participants_command(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):

        return

    if not context.args:

        await update.message.reply_text(

            "مثال:\n"
            "<code>/participants 1</code>",

            parse_mode="HTML"

        )

        return

    try:

        lottery_id = int(
            context.args[0]
        )

    except:

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

            "👥 تعداد شرکت‌کنندگان: 0"

        )

        return

    text = (

        f"👥 <b>شرکت‌کنندگان</b>\n\n"

        f"🎁 {lottery['title']}\n"

        f"📊 تعداد: <b>{len(users)}</b>\n\n"

    )

    for index, user in enumerate(
        users,
        1
    ):

        username = (

            "@"
            + user["username"]

            if user["username"]

            else "ندارد"

        )

        text += (

            f"{index}. "
            f"{user['first_name'] or 'بدون نام'}\n"

            f"🔗 {username}\n"

            f"🆔 <code>{user['user_id']}</code>\n\n"

        )

    # محدودیت پیام تلگرام
    for i in range(
        0,
        len(text),
        3800
    ):

        await update.message.reply_text(

            text[i:i + 3800],

            parse_mode="HTML"

        )


# =========================================================
# CANCEL
# =========================================================

async def cancel_command(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):

        return

    if not context.args:

        await update.message.reply_text(

            "مثال:\n"
            "<code>/cancel 1</code>",

            parse_mode="HTML"

        )

        return

    try:

        lottery_id = int(
            context.args[0]
        )

    except:

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

                callback_data=
                f"cancel_yes:{lottery_id}"

            ),

            InlineKeyboardButton(

                "❌ خیر",

                callback_data=
                "admin:menu"

            )

        ]

    ])

    await update.message.reply_text(

        f"⚠️ آیا قرعه‌کشی\n"
        f"<b>{lottery['title']}</b>\n"
        "لغو شود؟",

        parse_mode="HTML",

        reply_markup=keyboard

    )


async def cancel_yes(
    update,
    context
):

    query = update.callback_query

    if not is_admin(
        query.from_user.id
    ):

        await query.answer(
            "⛔ دسترسی ندارید.",
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

    con = get_db()

    con.execute(

        """
        UPDATE lotteries

        SET status = 'cancelled'

        WHERE id = ?

        AND status = 'active'
        """,

        (lottery_id,)

    )

    con.commit()

    con.close()

    cancelled_message = (

        "🚫 <b>این قرعه‌کشی لغو شد.</b>\n\n"

        f"🎁 جایزه: "
        f"<b>{lottery['title']}</b>\n"

        f"👥 تعداد شرکت‌کنندگان: "
        f"<b>{participant_count(lottery_id)}</b>\n\n"

        "⛔ دیگر امکان شرکت وجود ندارد."

    )

    # پیام کانال هم ویرایش می‌شود.
    if lottery["channel_message_id"]:

        try:

            await context.bot.edit_message_text(

                chat_id=CHANNEL,

                message_id=lottery[
                    "channel_message_id"
                ],

                text=cancelled_message,

                parse_mode="HTML",

                reply_markup=None

            )

        except TelegramError:

            pass

    await query.answer(
        "قرعه‌کشی لغو شد.",
        show_alert=True
    )

    await query.edit_message_text(

        "✅ قرعه‌کشی با موفقیت لغو شد.",

        reply_markup=admin_keyboard()

    )


# =========================================================
# ADMIN PANEL
# =========================================================

async def admin_command(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):

        return

    await update.message.reply_text(

        "🛠 <b>پنل مدیریت لاتاری میویی</b>\n\n"
        "دستور موردنظر را انتخاب کن:",

        parse_mode="HTML",

        reply_markup=admin_keyboard()

    )


async def admin_callback(
    update,
    context
):

    query = update.callback_query

    if not is_admin(
        query.from_user.id
    ):

        await query.answer(
            "⛔ دسترسی ندارید.",
            show_alert=True
        )

        return

    data = query.data

    await query.answer()

    # NEW
    if data == "admin:new":

        context.user_data.clear()

        context.user_data[
            "step"
        ] = "title"

        await query.message.reply_text(

            "🎁 عنوان جایزه را بفرست:"

        )

        return

    # MENU
    if data == "admin:menu":

        await query.edit_message_text(

            "🛠 <b>پنل مدیریت لاتاری میویی</b>",

            parse_mode="HTML",

            reply_markup=admin_keyboard()

        )

        return

    # ACTIVE
    if data == "admin:list":

        con = get_db()

        lotteries = con.execute(

            """
            SELECT *

            FROM lotteries

            WHERE status = 'active'

            ORDER BY id DESC
            """

        ).fetchall()

        con.close()

        if not lotteries:

            text = (
                "📋 هیچ قرعه‌کشی فعالی وجود ندارد."
            )

        else:

            text = (
                "📋 <b>قرعه‌کشی‌های فعال</b>\n\n"
            )

            for lottery in lotteries:

                text += (

                    f"🆔 #{lottery['id']}\n"

                    f"🎁 {lottery['title']}\n"

                    f"👥 "
                    f"{participant_count(lottery['id'])}\n"

                    f"⏱ "
                    f"{remaining_time(lottery['end_time'])}\n\n"

                )

        await query.edit_message_text(

            text,

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup([

                [

                    InlineKeyboardButton(

                        "↩️ بازگشت",

                        callback_data="admin:menu"

                    )

                ]

            ])

        )

        return

    # DRAW
    if data == "admin:draw":

        await query.edit_message_text(

            "🎲 <b>قرعه‌کشی دستی</b>\n\n"

            "برای اجرای زودتر از موعد:\n\n"

            "<code>/draw ID</code>\n\n"

            "مثال:\n"

            "<code>/draw 5</code>",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup([

                [

                    InlineKeyboardButton(

                        "↩️ بازگشت",

                        callback_data="admin:menu"

                    )

                ]

            ])

        )

        return

    # CANCEL
    if data == "admin:cancel":

        con = get_db()

        lotteries = con.execute(

            """
            SELECT *

            FROM lotteries

            WHERE status = 'active'

            ORDER BY id DESC
            """

        ).fetchall()

        con.close()

        buttons = []

        for lottery in lotteries:

            buttons.append([

                InlineKeyboardButton(

                    f"❌ #{lottery['id']} "
                    f"{lottery['title'][:25]}",

                    callback_data=
                    f"cancel_select:{lottery['id']}"

                )

            ])

        buttons.append([

            InlineKeyboardButton(

                "↩️ بازگشت",

                callback_data="admin:menu"

            )

        ])

        await query.edit_message_text(

            "❌ قرعه‌کشی موردنظر را انتخاب کن:",

            reply_markup=
            InlineKeyboardMarkup(buttons)

        )

        return

    # CANCEL SELECT
    if data.startswith(
        "cancel_select:"
    ):

        lottery_id = int(
            data.split(":")[1]
        )

        lottery = get_lottery(
            lottery_id
        )

        keyboard = InlineKeyboardMarkup([

            [

                InlineKeyboardButton(

                    "✅ تأیید لغو",

                    callback_data=
                    f"cancel_yes:{lottery_id}"

                ),

                InlineKeyboardButton(

                    "❌ خیر",

                    callback_data=
                    "admin:menu"

                )

            ]

        ])

        await query.edit_message_text(

            f"⚠️ <b>تأیید لغو</b>\n\n"

            f"🎁 {lottery['title']}\n"

            f"👥 شرکت‌کنندگان: "
            f"{participant_count(lottery_id)}\n\n"

            "آیا مطمئن هستی؟",

            parse_mode="HTML",

            reply_markup=keyboard

        )

        return

    # PARTICIPANTS
    if data == "admin:participants":

        con = get_db()

        lotteries = con.execute(

            """
            SELECT *

            FROM lotteries

            ORDER BY id DESC

            LIMIT 15
            """

        ).fetchall()

        con.close()

        buttons = []

        for lottery in lotteries:

            buttons.append([

                InlineKeyboardButton(

                    f"👥 #{lottery['id']} "
                    f"{lottery['title'][:25]}",

                    callback_data=
                    f"participants:{lottery['id']}"

                )

            ])

        buttons.append([

            InlineKeyboardButton(

                "↩️ بازگشت",

                callback_data="admin:menu"

            )

        ])

        await query.edit_message_text(

            "👥 قرعه‌کشی را انتخاب کن:",

            reply_markup=
            InlineKeyboardMarkup(buttons)

        )

        return

    # SHOW PARTICIPANTS
    if data.startswith(
        "participants:"
    ):

        lottery_id = int(
            data.split(":")[1]
        )

        lottery = get_lottery(
            lottery_id
        )

        users = get_participants(
            lottery_id
        )

        text = (

            f"👥 <b>{lottery['title']}</b>\n\n"

            f"📊 تعداد: "
            f"<b>{len(users)}</b>\n\n"

        )

        for index, user in enumerate(
            users,
            1
        ):

            username = (

                "@"
                + user["username"]

                if user["username"]

                else "ندارد"

            )

            text += (

                f"{index}. "
                f"{user['first_name'] or 'بدون نام'}\n"

                f"🔗 {username}\n"

                f"🆔 <code>{user['user_id']}</code>\n\n"

            )

        if not users:

            text += (
                "هنوز کسی شرکت نکرده."
            )

        await query.edit_message_text(

            text[:4000],

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup([

                [

                    InlineKeyboardButton(

                        "↩️ بازگشت",

                        callback_data=
                        "admin:participants"

                    )

                ]

            ])

        )

        return

    # HELP
    if data == "admin:help":

        await query.edit_message_text(

            "📚 <b>راهنمای ادمین</b>\n\n"

            "🎁 ساخت قرعه‌کشی\n"

            "📋 مشاهده قرعه‌کشی‌های فعال\n"

            "👥 مشاهده شرکت‌کنندگان\n"

            "🎲 قرعه‌کشی دستی قبل از پایان تایمر\n"

            "❌ لغو قرعه‌کشی با تأیید\n\n"

            "دستورات:\n"

            "<code>/admin</code>\n"
            "<code>/new</code>\n"
            "<code>/draw ID</code>\n"
            "<code>/cancel ID</code>\n"
            "<code>/participants ID</code>",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup([

                [

                    InlineKeyboardButton(

                        "↩️ بازگشت",

                        callback_data="admin:menu"

                    )

                ]

            ])

        )

        return


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

        target=run_web_server,

        daemon=True

    ).start()

    application = (

        Application

        .builder()

        .token(TOKEN)

        .build()

    )

    # Commands
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
            "cancel",
            cancel_command
        )

    )

    application.add_handler(

        CommandHandler(
            "participants",
            participants_command
        )

    )

    # Admin callbacks
    application.add_handler(

        CallbackQueryHandler(
            admin_callback,
            pattern=r"^(admin:|cancel_select:|participants:|cancel_yes:)"
        )

    )

    # Intro check
    application.add_handler(

        CallbackQueryHandler(
            check_intro,
            pattern=r"^check_intro$"
        )

    )

    # Lottery recheck
    application.add_handler(

        CallbackQueryHandler(
            recheck_lottery,
            pattern=r"^recheck:"
        )

    )

    # Lottery join
    application.add_handler(

        CallbackQueryHandler(
            join_lottery,
            pattern=r"^join:"
        )

    )

    # Admin text input
    application.add_handler(

        MessageHandler(

            filters.TEXT
            & ~filters.COMMAND,

            admin_input

        )

    )

    # Automatic draw.
    # Every 30 seconds checks expired lotteries.
    application.job_queue.run_repeating(

        auto_draw_job,

        interval=30,

        first=10

    )

    # Channel update every minute.
    # Timer + participant count + button.
    application.job_queue.run_repeating(

        update_channel_job,

        interval=60,

        first=10

    )

    print(
        "MEOW LOTTERY BOT STARTED"
    )

    application.run_polling()


if __name__ == "__main__":

    main()

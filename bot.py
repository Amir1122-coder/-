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


# =========================================================
# CONFIG
# =========================================================

TOKEN = os.getenv("BOT_TOKEN")

ADMIN_ID = int(
    os.getenv("ADMIN_ID", "7111630140")
)

CHANNEL = os.getenv(
    "ANNOUNCE_CHANNEL",
    "@meow_lottery"
)

DB = "lottery.db"

app = Flask(__name__)


# =========================================================
# WEB SERVER
# =========================================================

@app.route("/")
def home():
    return "Meow Lottery Bot is running."


def web_server():
    port = int(
        os.getenv("PORT", "10000")
    )

    app.run(
        host="0.0.0.0",
        port=port
    )


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
            message_id INTEGER DEFAULT NULL
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

    # Support old databases
    lottery_columns = {
        row["name"]
        for row in con.execute(
            "PRAGMA table_info(lotteries)"
        ).fetchall()
    }

    if "message_id" not in lottery_columns:
        con.execute(
            """
            ALTER TABLE lotteries
            ADD COLUMN message_id INTEGER
            """
        )

    participant_columns = {
        row["name"]
        for row in con.execute(
            "PRAGMA table_info(participants)"
        ).fetchall()
    }

    if "joined_at" not in participant_columns:
        con.execute(
            """
            ALTER TABLE participants
            ADD COLUMN joined_at TEXT DEFAULT ''
            """
        )

    con.commit()
    con.close()


# =========================================================
# GENERAL HELPERS
# =========================================================

def is_admin(user_id):
    return user_id == ADMIN_ID


def get_lottery(lottery_id):
    con = db()

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
    con = db()

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


def get_all_lotteries():
    con = db()

    rows = con.execute(
        """
        SELECT *
        FROM lotteries
        ORDER BY id DESC
        """
    ).fetchall()

    con.close()

    return rows


def get_participants(lottery_id):
    con = db()

    rows = con.execute(
        """
        SELECT *
        FROM participants
        WHERE lottery_id=?
        ORDER BY joined_at ASC
        """,
        (lottery_id,)
    ).fetchall()

    con.close()

    return rows


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


def set_lottery_status(
    lottery_id,
    status
):
    con = db()

    cursor = con.execute(
        """
        UPDATE lotteries
        SET status=?
        WHERE id=?
        """,
        (
            status,
            lottery_id
        )
    )

    con.commit()
    con.close()

    return cursor.rowcount > 0


# =========================================================
# TIME
# =========================================================

def parse_datetime(value):
    try:
        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt

    except Exception:
        return datetime.now(
            timezone.utc
        )


def remaining_seconds(end_time):
    end = parse_datetime(end_time)

    seconds = int(
        (
            end
            - datetime.now(timezone.utc)
        ).total_seconds()
    )

    return max(
        0,
        seconds
    )


def format_timer(seconds):
    if seconds <= 0:
        return "⛔ تمام شده"

    days, rem = divmod(
        seconds,
        86400
    )

    hours, rem = divmod(
        rem,
        3600
    )

    minutes, seconds = divmod(
        rem,
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


# =========================================================
# LINKS
# =========================================================

def lottery_link(
    lottery_id,
    bot_username
):
    return (
        f"https://t.me/"
        f"{bot_username}"
        f"?start=lottery_{lottery_id}"
    )


# =========================================================
# LOTTERY TEXT
# =========================================================

def lottery_text(
    lottery,
    bot_username=None,
    include_link=False
):
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

    # لینک فقط در کانال
    if (
        include_link
        and bot_username
    ):
        text += (
            "\n\n"
            "🔗 لینک شرکت:\n"
            f"{lottery_link(lottery['id'], bot_username)}"
        )

    return text


def cancelled_text(lottery):
    count = participant_count(
        lottery["id"]
    )

    return (
        "❌ <b>این قرعه‌کشی لغو شد</b>\n\n"
        f"🎁 جایزه: "
        f"<b>{lottery['title']}</b>\n"
        f"👥 تعداد شرکت‌کنندگان: "
        f"<b>{count}</b>\n\n"
        "⛔ امکان شرکت در این قرعه‌کشی وجود ندارد."
    )


def finished_text(
    lottery,
    winner_count=None
):
    count = participant_count(
        lottery["id"]
    )

    if winner_count is None:
        winner_count = min(
            lottery["winners"],
            count
        )

    return (
        "🎉 <b>قرعه‌کشی به پایان رسید</b>\n\n"
        f"🎁 جایزه: "
        f"<b>{lottery['title']}</b>\n"
        f"👥 شرکت‌کنندگان: "
        f"<b>{count}</b>\n"
        f"🏆 برنده‌ها: "
        f"<b>{winner_count}</b>\n\n"
        "⛔ امکان شرکت کردن وجود ندارد."
    )


# =========================================================
# BUTTONS
# =========================================================

def join_button(lottery_id):
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


def cancelled_button():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "❌ قرعه‌کشی لغو شده",
                callback_data="cancelled"
            )
        ]
    ])


# =========================================================
# ADMIN PANEL
# =========================================================

def admin_panel():
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
                callback_data="admin:active"
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
    ])


async def admin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return

    if not is_admin(
        update.effective_user.id
    ):
        return

    await update.message.reply_text(
        "⚙️ <b>پنل مدیریت Meow Lottery</b>\n\n"
        "یکی از گزینه‌ها را انتخاب کن:",
        parse_mode="HTML",
        reply_markup=admin_panel()
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

    if not context.args:
        if is_admin(
            update.effective_user.id
        ):
            await update.message.reply_text(
                "⚙️ پنل مدیریت:",
                reply_markup=admin_panel()
            )
        else:
            await update.message.reply_text(
                "🎰 به ربات قرعه‌کشی خوش آمدید."
            )

        return

    arg = context.args[0]

    if not arg.startswith(
        "lottery_"
    ):
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

    if lottery["status"] == "cancelled":
        await update.message.reply_text(
            cancelled_text(lottery),
            parse_mode="HTML"
        )
        return

    if lottery["status"] == "drawn":
        await update.message.reply_text(
            finished_text(lottery),
            parse_mode="HTML"
        )
        return

    if remaining_seconds(
        lottery["end_time"]
    ) <= 0:

        await update.message.reply_text(
            "⛔ زمان این قرعه‌کشی تمام شده است."
        )
        return

    # در پیوی لینک نمایش داده نمی‌شود
    await update.message.reply_text(
        lottery_text(lottery),
        parse_mode="HTML",
        reply_markup=join_button(
            lottery_id
        )
    )


# =========================================================
# JOIN
# =========================================================

async def join(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    try:
        lottery_id = int(
            query.data.split(":")[1]
        )

    except (
        ValueError,
        IndexError
    ):
        await query.answer(
            "❌ اطلاعات نامعتبر است.",
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

    if remaining_seconds(
        lottery["end_time"]
    ) <= 0:

        await query.answer(
            "⛔ زمان قرعه‌کشی تمام شده.",
            show_alert=True
        )
        return

    # =====================================================
    # REQUIRED CHANNELS
    # =====================================================

    missing = []

    channels = (
        lottery["channels"]
        or ""
    )

    for channel in channels.split(","):

        channel = channel.strip()

        if not channel:
            continue

        clean = channel.replace(
            "@",
            ""
        )

        try:

            member = await context.bot.get_chat_member(
                f"@{clean}",
                query.from_user.id
            )

            if member.status in (
                "left",
                "kicked"
            ):
                missing.append(
                    clean
                )

        except Exception:
            missing.append(
                clean
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
                chat_id=query.from_user.id,
                text=(
                    "📢 برای شرکت در قرعه‌کشی "
                    "ابتدا باید عضو کانال‌های زیر شوید:"
                ),
                reply_markup=InlineKeyboardMarkup(
                    buttons
                )
            )

        except Exception:
            pass

        return

    # =====================================================
    # REGISTER
    # =====================================================

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
                query.from_user.username
                or "",
                query.from_user.first_name
                or "",
                datetime.now(
                    timezone.utc
                ).isoformat()
            )
        )

        con.commit()

        # پیام کوتاه روی دکمه
        await query.answer(
            "🎉 با موفقیت ثبت شد!",
            show_alert=True
        )

        # پیام کامل در پیوی
        try:

            await context.bot.send_message(
                chat_id=query.from_user.id,
                text=(
                    "🎉 <b>تبریک!</b>\n\n"
                    "✅ شما با موفقیت "
                    "در قرعه‌کشی شرکت کردید.\n\n"
                    f"🎁 جایزه: "
                    f"<b>{lottery['title']}</b>\n\n"
                    "🍀 موفق باشید!"
                ),
                parse_mode="HTML"
            )

        except Exception as error:

            print(
                "Participant DM error:",
                error
            )

    except sqlite3.IntegrityError:

        await query.answer(
            "ℹ️ شما قبلاً در این قرعه‌کشی شرکت کرده‌اید.",
            show_alert=True
        )

    finally:
        con.close()


# =========================================================
# NEW LOTTERY
# =========================================================

async def new_lottery(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return

    if not is_admin(
        update.effective_user.id
    ):
        return

    context.user_data.clear()

    context.user_data[
        "step"
    ] = "title"

    await update.message.reply_text(
        "🎁 عنوان جایزه را بفرست:"
    )


# =========================================================
# ADMIN INPUT
# =========================================================

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

    value = update.message.text.strip()

    # -----------------------------------------------------
    # TITLE
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # WINNERS
    # -----------------------------------------------------

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
            "⏱ چند ساعت فعال باشد؟\n\n"
            "مثال: 24"
        )

        return

    # -----------------------------------------------------
    # DURATION
    # -----------------------------------------------------

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
            "📢 کانال عضویت اجباری را بفرست.\n\n"
            "مثال:\n"
            "@channel\n\n"
            "اگر اجباری نیست:\n"
            "none"
        )

        return

    # -----------------------------------------------------
    # CHANNELS
    # -----------------------------------------------------

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

        lottery = get_lottery(
            lottery_id
        )

        try:

            bot_username = (
                context.bot.username
            )

            # لینک فقط در کانال
            text = lottery_text(
                lottery,
                bot_username,
                include_link=True
            )

            sent = await context.bot.send_message(
                chat_id=CHANNEL,
                text=text,
                parse_mode="HTML",
                reply_markup=join_button(
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
                "✅ قرعه‌کشی با موفقیت ساخته شد.\n\n"
                f"🆔 شناسه: {lottery_id}\n"
                f"🎁 جایزه: {title}\n"
                f"🏆 برنده‌ها: {winners}\n"
                f"⏱ مدت: {hours} ساعت"
            )

        except Exception as error:

            await update.message.reply_text(
                "❌ ارسال قرعه‌کشی به کانال ناموفق بود:\n"
                f"{error}"
            )

        context.user_data.clear()


# =========================================================
# ACTIVE LOTTERIES
# =========================================================

async def show_active_lotteries(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    lotteries = get_active_lotteries()

    if not lotteries:

        await update.callback_query.edit_message_text(
            "📭 هیچ قرعه‌کشی فعالی وجود ندارد.",
            reply_markup=admin_panel()
        )

        return

    buttons = []

    for lottery in lotteries:

        buttons.append([
            InlineKeyboardButton(
                (
                    f"#{lottery['id']} "
                    f"🎁 {lottery['title']}"
                ),
                callback_data=(
                    f"admin:view:"
                    f"{lottery['id']}"
                )
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔙 بازگشت",
            callback_data="admin:back"
        )
    ])

    await update.callback_query.edit_message_text(
        "📋 <b>قرعه‌کشی‌های فعال:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            buttons
        )
    )


# =========================================================
# PARTICIPANTS SELECT
# =========================================================

async def participants_select(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    lotteries = get_all_lotteries()

    if not lotteries:

        await update.callback_query.edit_message_text(
            "📭 هنوز قرعه‌کشی‌ای ساخته نشده.",
            reply_markup=admin_panel()
        )

        return

    buttons = []

    for lottery in lotteries[:20]:

        count = participant_count(
            lottery["id"]
        )

        buttons.append([
            InlineKeyboardButton(
                (
                    f"#{lottery['id']} "
                    f"👥 {count}"
                ),
                callback_data=(
                    f"admin:plist:"
                    f"{lottery['id']}"
                )
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔙 بازگشت",
            callback_data="admin:back"
        )
    ])

    await update.callback_query.edit_message_text(
        "👥 <b>یک قرعه‌کشی را انتخاب کن:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            buttons
        )
    )


# =========================================================
# PARTICIPANTS LIST
# =========================================================

async def send_participants(
    update,
    context,
    lottery_id
):
    lottery = get_lottery(
        lottery_id
    )

    if not lottery:

        await update.callback_query.answer(
            "❌ قرعه‌کشی پیدا نشد.",
            show_alert=True
        )

        return

    users = get_participants(
        lottery_id
    )

    if not users:

        await update.callback_query.message.reply_text(
            "📋 هیچ شرکت‌کننده‌ای وجود ندارد."
        )

        return

    text = (
        "👥 <b>شرکت‌کنندگان</b>\n\n"
        f"🎁 {lottery['title']}\n"
        f"🆔 قرعه‌کشی: "
        f"<code>{lottery_id}</code>\n"
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
            f"{index}. "
            f"👤 {name}\n"
            f"   🔗 {username}\n"
            f"   🆔 <code>{user['user_id']}</code>\n\n"
        )

        if len(text) + len(item) > 3800:

            await update.callback_query.message.reply_text(
                text,
                parse_mode="HTML"
            )

            text = ""

        text += item

    if text:

        await update.callback_query.message.reply_text(
            text,
            parse_mode="HTML"
        )


# =========================================================
# DRAW SELECT
# =========================================================

async def draw_select(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    lotteries = get_active_lotteries()

    if not lotteries:

        await update.callback_query.edit_message_text(
            "📭 هیچ قرعه‌کشی فعالی وجود ندارد.",
            reply_markup=admin_panel()
        )

        return

    buttons = []

    for lottery in lotteries:

        count = participant_count(
            lottery["id"]
        )

        buttons.append([
            InlineKeyboardButton(
                (
                    f"🎲 #{lottery['id']} "
                    f"👥 {count}"
                ),
                callback_data=(
                    f"admin:drawconfirm:"
                    f"{lottery['id']}"
                )
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔙 بازگشت",
            callback_data="admin:back"
        )
    ])

    await update.callback_query.edit_message_text(
        "🎲 <b>قرعه‌کشی موردنظر را انتخاب کن:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            buttons
        )
    )


# =========================================================
# DRAW CONFIRM
# =========================================================

async def draw_confirm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    lottery_id
):
    lottery = get_lottery(
        lottery_id
    )

    if not lottery:

        await update.callback_query.answer(
            "❌ قرعه‌کشی پیدا نشد.",
            show_alert=True
        )

        return

    count = participant_count(
        lottery_id
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎲 بله، اجرا شود",
                callback_data=(
                    f"admin:drawnow:"
                    f"{lottery_id}"
                )
            )
        ],
        [
            InlineKeyboardButton(
                "↩️ انصراف",
                callback_data="admin:back"
            )
        ]
    ])

    await update.callback_query.edit_message_text(
        "⚠️ <b>تأیید اجرای قرعه‌کشی</b>\n\n"
        f"🎁 {lottery['title']}\n"
        f"👥 شرکت‌کنندگان: {count}\n"
        f"🏆 حداکثر برنده: "
        f"{min(lottery['winners'], count)}\n\n"
        "آیا مطمئنی؟",
        parse_mode="HTML",
        reply_markup=keyboard
    )


# =========================================================
# CANCEL SELECT
# =========================================================

async def cancel_select(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    lotteries = get_active_lotteries()

    if not lotteries:

        await update.callback_query.edit_message_text(
            "📭 هیچ قرعه‌کشی فعالی وجود ندارد.",
            reply_markup=admin_panel()
        )

        return

    buttons = []

    for lottery in lotteries:

        buttons.append([
            InlineKeyboardButton(
                (
                    f"❌ #{lottery['id']} "
                    f"{lottery['title']}"
                ),
                callback_data=(
                    f"admin:cancelconfirm:"
                    f"{lottery['id']}"
                )
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔙 بازگشت",
            callback_data="admin:back"
        )
    ])

    await update.callback_query.edit_message_text(
        "❌ <b>قرعه‌کشی موردنظر را انتخاب کن:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            buttons
        )
    )


# =========================================================
# CANCEL CONFIRM
# =========================================================

async def cancel_confirm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    lottery_id
):
    lottery = get_lottery(
        lottery_id
    )

    if not lottery:

        await update.callback_query.answer(
            "❌ قرعه‌کشی پیدا نشد.",
            show_alert=True
        )

        return

    count = participant_count(
        lottery_id
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ بله، لغو شود",
                callback_data=(
                    f"admin:cancelnow:"
                    f"{lottery_id}"
                )
            )
        ],
        [
            InlineKeyboardButton(
                "↩️ انصراف",
                callback_data="admin:back"
            )
        ]
    ])

    await update.callback_query.edit_message_text(
        "⚠️ <b>تأیید لغو قرعه‌کشی</b>\n\n"
        f"🎁 جایزه: "
        f"<b>{lottery['title']}</b>\n"
        f"👥 شرکت‌کنندگان: "
        f"<b>{count}</b>\n\n"
        "❗ با لغو، دیگر کسی نمی‌تواند شرکت کند "
        "و پیام کانال نیز به حالت «لغو شد» تغییر می‌کند.\n\n"
        "آیا مطمئنی؟",
        parse_mode="HTML",
        reply_markup=keyboard
    )


# =========================================================
# CANCEL NOW
# =========================================================

async def cancel_now(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    lottery_id
):
    lottery = get_lottery(
        lottery_id
    )

    if not lottery:

        await update.callback_query.answer(
            "❌ پیدا نشد.",
            show_alert=True
        )

        return

    if lottery["status"] != "active":

        await update.callback_query.answer(
            "⛔ این قرعه‌کشی دیگر فعال نیست.",
            show_alert=True
        )

        return

    success = set_lottery_status(
        lottery_id,
        "cancelled"
    )

    if not success:

        await update.callback_query.answer(
            "❌ عملیات ناموفق بود.",
            show_alert=True
        )

        return

    # -----------------------------------------------------
    # EDIT CHANNEL MESSAGE
    # -----------------------------------------------------

    if lottery["message_id"]:

        try:

            await context.bot.edit_message_text(
                chat_id=CHANNEL,
                message_id=lottery["message_id"],
                text=cancelled_text(
                    lottery
                ),
                parse_mode="HTML",
                reply_markup=cancelled_button()
            )

        except Exception as error:

            print(
                "Cancel channel edit error:",
                error
            )

    await update.callback_query.answer(
        "✅ قرعه‌کشی لغو شد.",
        show_alert=True
    )

    await update.callback_query.edit_message_text(
        "✅ <b>قرعه‌کشی با موفقیت لغو شد.</b>",
        parse_mode="HTML",
        reply_markup=admin_panel()
    )


# =========================================================
# PERFORM DRAW
# =========================================================

async def perform_draw(
    lottery_id,
    context
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

    count = len(users)

    # تعداد برنده هرگز بیشتر از شرکت‌کنندگان نیست
    winner_count = min(
        int(lottery["winners"]),
        count
    )

    # -----------------------------------------------------
    # NO PARTICIPANTS
    # -----------------------------------------------------

    if count == 0:

        con = db()

        cursor = con.execute(
            """
            UPDATE lotteries
            SET status='drawn'
            WHERE id=?
            AND status='active'
            """,
            (lottery_id,)
        )

        con.commit()
        con.close()

        if cursor.rowcount == 0:
            return False

        if lottery["message_id"]:

            try:

                await context.bot.edit_message_text(
                    chat_id=CHANNEL,
                    message_id=lottery["message_id"],
                    text=(
                        "⛔ <b>قرعه‌کشی به پایان رسید</b>\n\n"
                        f"🎁 جایزه: "
                        f"<b>{lottery['title']}</b>\n\n"
                        "هیچ شرکت‌کننده‌ای وجود نداشت."
                    ),
                    parse_mode="HTML",
                    reply_markup=finished_button()
                )

            except Exception as error:

                print(
                    "No participants edit error:",
                    error
                )

        return True

    # -----------------------------------------------------
    # RANDOM WINNERS
    # -----------------------------------------------------

    winners = random.sample(
        users,
        winner_count
    )

    # Lock lottery
    con = db()

    cursor = con.execute(
        """
        UPDATE lotteries
        SET status='drawn'
        WHERE id=?
        AND status='active'
        """,
        (lottery_id,)
    )

    con.commit()
    con.close()

    if cursor.rowcount == 0:
        return False

    # -----------------------------------------------------
    # RESULT TEXT
    # -----------------------------------------------------

    result = (
        "🎊 <b>نتیجه قرعه‌کشی</b>\n\n"
        f"🎁 جایزه: "
        f"<b>{lottery['title']}</b>\n"
        f"👥 شرکت‌کنندگان: "
        f"<b>{count}</b>\n"
        f"🏆 تعداد برنده‌ها: "
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
    # ADMIN
    # -----------------------------------------------------

    try:

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=result,
            parse_mode="HTML"
        )

    except Exception as error:

        print(
            "Admin result error:",
            error
        )

    # -----------------------------------------------------
    # CHANNEL
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
            "Channel result error:",
            error
        )

    # -----------------------------------------------------
    # WINNER PRIVATE MESSAGE
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
    # EDIT ORIGINAL CHANNEL MESSAGE
    # -----------------------------------------------------

    if lottery["message_id"]:

        try:

            await context.bot.edit_message_text(
                chat_id=CHANNEL,
                message_id=lottery["message_id"],
                text=finished_text(
                    lottery,
                    winner_count
                ),
                parse_mode="HTML",
                reply_markup=finished_button()
            )

        except Exception as error:

            print(
                "Original lottery edit error:",
                error
            )

    return True


# =========================================================
# MANUAL DRAW COMMAND
# =========================================================

async def draw_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return

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
# PARTICIPANTS COMMAND
# =========================================================

async def participants_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return

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
            "📋 هیچ شرکت‌کننده‌ای وجود ندارد."
        )

        return

    text = (
        "👥 <b>لیست شرکت‌کنندگان</b>\n\n"
        f"🎁 {lottery['title']}\n"
        f"🆔 قرعه‌کشی: "
        f"<code>{lottery_id}</code>\n"
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

            end_time = parse_datetime(
                lottery["end_time"]
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
# UPDATE CHANNEL MESSAGE
# =========================================================

async def update_lottery_messages(
    context: ContextTypes.DEFAULT_TYPE
):
    lotteries = get_active_lotteries()

    bot_username = (
        context.bot.username
    )

    if not bot_username:
        return

    for lottery in lotteries:

        message_id = lottery[
            "message_id"
        ]

        if not message_id:
            continue

        seconds = remaining_seconds(
            lottery["end_time"]
        )

        if seconds <= 0:
            continue

        try:

            # لینک همیشه حفظ می‌شود
            # دکمه همیشه حفظ می‌شود
            # تعداد شرکت‌کنندگان آپدیت می‌شود
            # تایمر نیز دقیقاً از end_time محاسبه می‌شود

            text = lottery_text(
                lottery,
                bot_username,
                include_link=True
            )

            await context.bot.edit_message_text(
                chat_id=CHANNEL,
                message_id=message_id,
                text=text,
                parse_mode="HTML",
                reply_markup=join_button(
                    lottery["id"]
                )
            )

        except Exception as error:

            print(
                f"Message update error "
                f"{lottery['id']}: {error}"
            )


# =========================================================
# ADMIN CALLBACKS
# =========================================================

async def admin_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
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

    # -----------------------------------------------------
    # BACK
    # -----------------------------------------------------

    if data == "admin:back":

        await query.edit_message_text(
            "⚙️ <b>پنل مدیریت</b>",
            parse_mode="HTML",
            reply_markup=admin_panel()
        )

        return

    # -----------------------------------------------------
    # NEW
    # -----------------------------------------------------

    if data == "admin:new":

        context.user_data.clear()

        context.user_data[
            "step"
        ] = "title"

        await query.edit_message_text(
            "🎁 عنوان جایزه را بفرست:"
        )

        return

    # -----------------------------------------------------
    # ACTIVE
    # -----------------------------------------------------

    if data == "admin:active":

        await show_active_lotteries(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # PARTICIPANTS
    # -----------------------------------------------------

    if data == "admin:participants":

        await participants_select(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # DRAW
    # -----------------------------------------------------

    if data == "admin:draw":

        await draw_select(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # CANCEL
    # -----------------------------------------------------

    if data == "admin:cancel":

        await cancel_select(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # VIEW
    # -----------------------------------------------------

    if data.startswith(
        "admin:view:"
    ):

        lottery_id = int(
            data.split(":")[2]
        )

        lottery = get_lottery(
            lottery_id
        )

        if not lottery:

            await query.edit_message_text(
                "❌ قرعه‌کشی پیدا نشد.",
                reply_markup=admin_panel()
            )

            return

        count = participant_count(
            lottery_id
        )

        timer = format_timer(
            remaining_seconds(
                lottery["end_time"]
            )
        )

        await query.edit_message_text(
            (
                f"🎁 <b>{lottery['title']}</b>\n\n"
                f"🆔 شناسه: {lottery_id}\n"
                f"👥 شرکت‌کنندگان: {count}\n"
                f"🏆 برنده‌ها: {lottery['winners']}\n"
                f"⏱ {timer}\n"
                f"📌 وضعیت: {lottery['status']}"
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 بازگشت",
                        callback_data="admin:active"
                    )
                ]
            ])
        )

        return

    # -----------------------------------------------------
    # PARTICIPANT LIST
    # -----------------------------------------------------

    if data.startswith(
        "admin:plist:"
    ):

        lottery_id = int(
            data.split(":")[2]
        )

        await send_participants(
            update,
            context,
            lottery_id
        )

        return

    # -----------------------------------------------------
    # DRAW CONFIRM
    # -----------------------------------------------------

    if data.startswith(
        "admin:drawconfirm:"
    ):

        lottery_id = int(
            data.split(":")[2]
        )

        await draw_confirm(
            update,
            context,
            lottery_id
        )

        return

    # -----------------------------------------------------
    # DRAW NOW
    # -----------------------------------------------------

    if data.startswith(
        "admin:drawnow:"
    ):

        lottery_id = int(
            data.split(":")[2]
        )

        success = await perform_draw(
            lottery_id,
            context
        )

        if success:

            await query.edit_message_text(
                "✅ <b>قرعه‌کشی انجام شد.</b>",
                parse_mode="HTML",
                reply_markup=admin_panel()
            )

        else:

            await query.edit_message_text(
                "❌ اجرای قرعه‌کشی ناموفق بود.",
                reply_markup=admin_panel()
            )

        return

    # -----------------------------------------------------
    # CANCEL CONFIRM
    # -----------------------------------------------------

    if data.startswith(
        "admin:cancelconfirm:"
    ):

        lottery_id = int(
            data.split(":")[2]
        )

        await cancel_confirm(
            update,
            context,
            lottery_id
        )

        return

    # -----------------------------------------------------
    # CANCEL NOW
    # -----------------------------------------------------

    if data.startswith(
        "admin:cancelnow:"
    ):

        lottery_id = int(
            data.split(":")[2]
        )

        await cancel_now(
            update,
            context,
            lottery_id
        )

        return


# =========================================================
# SIMPLE CALLBACKS
# =========================================================

async def finished_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.callback_query.answer(
        "⛔ این قرعه‌کشی تمام شده است.",
        show_alert=True
    )


async def cancelled_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.callback_query.answer(
        "❌ این قرعه‌کشی لغو شده است.",
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

    # Render Web Server
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

    # =====================================================
    # JOBS
    # =====================================================

    if application.job_queue:

        # بررسی پایان قرعه‌کشی
        # هر 10 ثانیه
        application.job_queue.run_repeating(
            auto_draw_job,
            interval=10,
            first=5
        )

        # آپدیت تایمر و تعداد شرکت‌کنندگان
        # هر 10 ثانیه
        application.job_queue.run_repeating(
            update_lottery_messages,
            interval=10,
            first=10
        )

    # =====================================================
    # COMMANDS
    # =====================================================

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

    # =====================================================
    # JOIN
    # =====================================================

    application.add_handler(
        CallbackQueryHandler(
            join,
            pattern=r"^join:"
        )
    )

    # =====================================================
    # ADMIN PANEL
    # =====================================================

    application.add_handler(
        CallbackQueryHandler(
            admin_callback,
            pattern=r"^admin:"
        )
    )

    # =====================================================
    # FINISHED / CANCELLED
    # =====================================================

    application.add_handler(
        CallbackQueryHandler(
            finished_callback,
            pattern=r"^finished$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            cancelled_callback,
            pattern=r"^cancelled$"
        )
    )

    # =====================================================
    # ADMIN TEXT INPUT
    # =====================================================

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            admin_input
        )
    )

    print(
        "===================================="
    )

    print(
        "MEOW LOTTERY BOT STARTED"
    )

    print(
        "AUTO DRAW: ON"
    )

    print(
        "TIMER UPDATE: 10 SEC"
    )

    print(
        "PARTICIPANT UPDATE: 10 SEC"
    )

    print(
        "ADMIN PANEL: ON"
    )

    print(
        "CANCEL CONFIRMATION: ON"
    )

    print(
        "===================================="
    )

    application.run_polling()


if __name__ == "__main__":
    main()

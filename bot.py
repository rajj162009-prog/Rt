import os
import sys
import html
import json
import socket
import random
import asyncio
from datetime import datetime
import httpx
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.request import HTTPXRequest
from telegram.error import Conflict, NetworkError, TimedOut
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    FloodWaitError,
    SlowModeWaitError,
    ChatWriteForbiddenError,
    ChannelPrivateError,
    UserBannedInChannelError,
)

# 1. Force IPv4 globally to stop Render IPv6 ReadTimeout hangs
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_getaddrinfo(*args, **kwargs):
    res = _orig_getaddrinfo(*args, **kwargs)
    ipv4 = [r for r in res if r[0] == socket.AF_INET]
    return ipv4 if ipv4 else res


socket.getaddrinfo = _ipv4_getaddrinfo

# 2. Load & Validate Environment Variables
raw_api_id = os.environ.get("API_ID", "").strip()
API_HASH = os.environ.get("API_HASH", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
raw_owner_id = os.environ.get("OWNER_ID", "0").strip()

if not raw_api_id.isdigit() or not API_HASH or not BOT_TOKEN:
    print("FATAL ERROR: Render Environment Variables mein API_ID, API_HASH, aur BOT_TOKEN check karein!")
    sys.exit(1)

API_ID = int(raw_api_id)
OWNER_ID = int(raw_owner_id) if raw_owner_id.isdigit() else 0

DATA_FILE = "bot_data.json"


def load_data():
    default_data = {
        "session": os.environ.get("SESSION_STRING", "").strip(),
        "ad_text": os.environ.get("DEFAULT_AD", "").strip(),
        "delay": int(os.environ.get("DEFAULT_DELAY", "120")),
        "running": False,
        "ad_counter": 1,
    }
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in default_data.items():
                    if k not in data:
                        data[k] = v
                if not data.get("session") and os.environ.get("SESSION_STRING", "").strip():
                    data["session"] = os.environ.get("SESSION_STRING", "").strip()
                return data
        except Exception:
            pass
    return default_data


def save_data(data):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"File save warning: {e}")


config = load_data()
user_states = {}
ad_task = None
bot_app = None

# Live runtime stats
stats = {
    "rounds_completed": 0,
    "total_sent": 0,
    "last_round_sent": 0,
    "last_round_skipped": 0,
    "last_run_time": "Abhi tak nahi chala",
    "last_status": "Idle",
}


def create_telethon_client(session_str=""):
    return TelegramClient(
        StringSession(session_str),
        API_ID,
        API_HASH,
        device_model="Samsung Galaxy S23",
        system_version="Android 14",
        app_version="10.14.5",
        lang_code="en",
        system_lang_code="en-US",
        connection_retries=5,
        retry_delay=3,
        timeout=30,
    )


def format_human_ad(base_text):
    current_num = int(config.get("ad_counter", 1))
    safe_text = html.escape(base_text)
    return f"<b>{safe_text}\n{current_num}</b>"


def get_main_menu():
    acc_status = "Logged In" if config.get("session") else "Not Logged In"
    ad_status = "Set" if config.get("ad_text") else "Not Set"
    run_status = "RUNNING" if config.get("running") else "STOPPED"
    next_num = int(config.get("ad_counter", 1))

    text = (
        f"<b>Ad Bot Control Panel</b>\n\n"
        f"• <b>Account:</b> {acc_status}\n"
        f"• <b>Ad Text:</b> {ad_status}\n"
        f"• <b>Round Delay:</b> {config.get('delay')} sec\n"
        f"• <b>Next Ad Number:</b> {next_num}\n"
        f"• <b>Bot Status:</b> {run_status}\n"
        f"• <b>Total Ads Sent:</b> {stats['total_sent']} (Rounds: {stats['rounds_completed']})\n"
        f"• <b>Current State:</b> {html.escape(stats['last_status'])}"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("1. Account Login", callback_data="login_acc")],
            [InlineKeyboardButton("2. Set Ad Message", callback_data="set_ad")],
            [InlineKeyboardButton("3. Set Time Delay", callback_data="set_delay")],
            [
                InlineKeyboardButton("Start Bot", callback_data="start_bot"),
                InlineKeyboardButton("Stop Bot", callback_data="stop_bot"),
            ],
            [InlineKeyboardButton("Refresh Status & Preview", callback_data="refresh_status")],
        ]
    )
    return text, keyboard


def cancel_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Cancel", callback_data="cancel")]]
    )


async def notify_owner(text):
    if bot_app and OWNER_ID:
        try:
            await bot_app.bot.send_message(chat_id=OWNER_ID, text=text, parse_mode="HTML")
        except Exception as e:
            print(f"Notify owner warning: {e}")


async def ad_worker():
    global config
    if not config.get("session"):
        config["running"] = False
        stats["last_status"] = "Stopped (No account logged in)"
        save_data(config)
        return

    user_client = create_telethon_client(config["session"])
    try:
        stats["last_status"] = "Connecting to Telegram..."
        await user_client.connect()

        if not await user_client.is_user_authorized():
            config["running"] = False
            config["session"] = ""
            stats["last_status"] = "Session Expired - Dobara Login Karein"
            save_data(config)
            await notify_owner("Account session expire ho gaya hai. Dobara <b>1. Account Login</b> karein.")
            return

        while config.get("running"):
            ad_text = config.get("ad_text", "").strip()
            if not ad_text:
                stats["last_status"] = "Waiting for Ad Message..."
                await asyncio.sleep(5)
                continue

            stats["last_status"] = "Fetching groups list..."
            groups = []
            async for dialog in user_client.iter_dialogs():
                if dialog.is_group:
                    groups.append(dialog)

            if not groups:
                stats["last_status"] = "No groups found in this account!"
                await notify_owner("Is account mein koi Telegram group join nahi hai. Pehle kuch groups join karein.")
                await asyncio.sleep(30)
                continue

            # Human behavior: randomize group order every round
            random.shuffle(groups)

            sent_count = 0
            skipped_count = 0

            for idx, dialog in enumerate(groups, start=1):
                if not config.get("running"):
                    break

                try:
                    current_num = int(config.get("ad_counter", 1))
                    stats["last_status"] = f"Sending #{current_num} ({idx}/{len(groups)}): {dialog.name[:18]}"

                    # Human behavior 1: mark recent messages as read before typing
                    try:
                        await user_client.send_read_acknowledge(dialog.id)
                    except Exception:
                        pass

                    # Human behavior 2: realistic typing indicator
                    async with user_client.action(dialog.id, "typing"):
                        await asyncio.sleep(random.uniform(2.0, 4.2))

                    final_msg = format_human_ad(ad_text)
                    await user_client.send_message(dialog.id, final_msg, parse_mode="html")

                    # Increment counter after every successful send
                    config["ad_counter"] = current_num + 1
                    save_data(config)

                    sent_count += 1
                    stats["total_sent"] += 1

                    # Human behavior 3: natural pause between groups
                    await asyncio.sleep(random.uniform(4.0, 8.5))

                except FloodWaitError as e:
                    wait_sec = e.seconds + 5
                    stats["last_status"] = f"FloodWait: waiting {wait_sec}s..."
                    await asyncio.sleep(wait_sec)
                except (SlowModeWaitError, ChatWriteForbiddenError, ChannelPrivateError, UserBannedInChannelError):
                    skipped_count += 1
                    continue
                except Exception:
                    skipped_count += 1
                    continue

            if not config.get("running"):
                break

            stats["rounds_completed"] += 1
            stats["last_round_sent"] = sent_count
            stats["last_round_skipped"] = skipped_count
            stats["last_run_time"] = datetime.now().strftime("%H:%M:%S")

            base_delay = int(config.get("delay", 120))
            jitter = random.randint(8, 25)
            total_sleep = base_delay + jitter

            stats["last_status"] = f"Round #{stats['rounds_completed']} done ({sent_count} sent). Next in {total_sleep}s"

            await notify_owner(
                f"<b>Round #{stats['rounds_completed']} Complete</b>\n"
                f"• <b>Sent:</b> {sent_count} groups (Next Ad #: {config.get('ad_counter', 1)})\n"
                f"• <b>Skipped/Locked:</b> {skipped_count} groups\n"
                f"• <b>Next Round:</b> {total_sleep} seconds baad"
            )

            for remaining in range(total_sleep, 0, -1):
                if not config.get("running"):
                    break
                stats["last_status"] = f"Waiting {remaining}s for next round..."
                await asyncio.sleep(1)

    except Exception as e:
        stats["last_status"] = f"Error: {str(e)[:40]}"
        print(f"Ad worker exception: {e}")
    finally:
        if not config.get("running"):
            stats["last_status"] = "Stopped"
        try:
            await user_client.disconnect()
        except Exception:
            pass


def start_ad_loop():
    global ad_task
    if ad_task is None or ad_task.done():
        ad_task = asyncio.create_task(ad_worker())


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    uid = update.effective_user.id
    if OWNER_ID and uid != OWNER_ID:
        return

    user_states.pop(uid, None)
    text, keyboard = get_main_menu()
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.from_user:
        return
    uid = query.from_user.id
    if OWNER_ID and uid != OWNER_ID:
        return

    data = query.data

    if data == "login_acc":
        await query.answer()
        user_states[uid] = {"step": "WAIT_PHONE"}
        await query.edit_message_text(
            "Apna Telegram phone number country code ke saath bhejein (jaise <code>+919876543210</code>):",
            reply_markup=cancel_keyboard(),
            parse_mode="HTML",
        )

    elif data == "set_ad":
        await query.answer()
        user_states[uid] = {"step": "WAIT_AD"}
        await query.edit_message_text(
            "Apna Ad message bhejein (is text ke neeche bina blank line ke 1, 2, 3... number lagega aur poora message <b>Bold</b> format mein jayega):",
            reply_markup=cancel_keyboard(),
            parse_mode="HTML",
        )

    elif data == "set_delay":
        await query.answer()
        user_states[uid] = {"step": "WAIT_DELAY"}
        await query.edit_message_text(
            "Har round ke beech kitne seconds ka delay rakhna hai? Number bhejein (jaise <code>60</code> ya <code>300</code>):",
            reply_markup=cancel_keyboard(),
            parse_mode="HTML",
        )

    elif data == "start_bot":
        if not config.get("session"):
            await query.answer("Pehle 1. Account Login karein!", show_alert=True)
            return
        if not config.get("ad_text"):
            await query.answer("Pehle 2. Set Ad Message karein!", show_alert=True)
            return
        if config.get("running") and ad_task and not ad_task.done():
            await query.answer("Bot pehle se chal raha hai!", show_alert=True)
            return

        await query.answer("Ad Bot Start Ho Gaya!")
        config["running"] = True
        save_data(config)
        start_ad_loop()
        await asyncio.sleep(0.5)
        text, keyboard = get_main_menu()
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")

    elif data == "stop_bot":
        await query.answer("Ad Bot Stop Ho Gaya!")
        config["running"] = False
        stats["last_status"] = "Stopped by user"
        save_data(config)
        text, keyboard = get_main_menu()
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")

    elif data == "refresh_status":
        await query.answer("Status refreshed!")
        text, keyboard = get_main_menu()
        if config.get("ad_text"):
            sample = format_human_ad(config["ad_text"])
            text += f"\n\n<b>Next Ad Preview:</b>\n{sample}"
        try:
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
        except Exception:
            pass

    elif data == "cancel":
        await query.answer()
        state = user_states.pop(uid, None)
        if state and "client" in state:
            try:
                await state["client"].disconnect()
            except Exception:
                pass
        text, keyboard = get_main_menu()
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message or not update.message.text:
        return
    uid = update.effective_user.id
    if OWNER_ID and uid != OWNER_ID:
        return

    state = user_states.get(uid)
    if not state:
        return

    step = state.get("step")
    msg_text = update.message.text.strip()

    # Step 1: Phone Number
    if step == "WAIT_PHONE":
        client = create_telethon_client()
        try:
            await update.message.reply_text("OTP bheja ja raha hai, 2 second rukein...")
            await client.connect()
            sent = await client.send_code_request(msg_text)
            user_states[uid] = {
                "step": "WAIT_CODE",
                "phone": msg_text,
                "hash": sent.phone_code_hash,
                "client": client,
            }
            await update.message.reply_text(
                "Telegram par OTP aaya hoga.\n"
                "<b>IMPORTANT:</b> OTP ko <b>spaces ke saath</b> bhejein (jaise <code>1 2 3 4 5</code>), bina spaces ke bhejne par Telegram OTP block kar deta hai:",
                parse_mode="HTML",
            )
        except Exception as e:
            try:
                await client.disconnect()
            except Exception:
                pass
            user_states.pop(uid, None)
            await update.message.reply_text(
                f"OTP Error: {html.escape(str(e))}\nDobara /start bhejein.",
                parse_mode="HTML",
            )

    # Step 2: OTP Code -> Auto move to Set Ad
    elif step == "WAIT_CODE":
        code = msg_text.replace(" ", "").replace("-", "")
        client = state["client"]
        try:
            await client.sign_in(
                phone=state["phone"],
                code=code,
                phone_code_hash=state["hash"],
            )
            session_str = client.session.save()
            await client.disconnect()
            config["session"] = session_str
            save_data(config)

            user_states[uid] = {"step": "WAIT_AD", "auto_flow": True}
            await update.message.reply_text(
                "Account successfully login ho gaya!\n\n"
                "<b>Permanent Login Backup (Optional):</b> Agar aap chahte hain ki Render restart hone par bhi kabhi logout na ho, toh Render Environment Variables mein <code>SESSION_STRING</code> key banakar yeh value paste kar dein:\n"
                f"<code>{html.escape(session_str)}</code>\n\n"
                "Ab agla step: Apna <b>Ad Message</b> bhejein:",
                parse_mode="HTML",
            )

        except SessionPasswordNeededError:
            state["step"] = "WAIT_2FA"
            await update.message.reply_text(
                "Is account par 2-Step Verification (Password) laga hai. Apna password bhejein:"
            )
        except Exception as e:
            try:
                await client.disconnect()
            except Exception:
                pass
            user_states.pop(uid, None)
            await update.message.reply_text(
                f"Login failed: {html.escape(str(e))}\nDobara /start bhejein.",
                parse_mode="HTML",
            )

    # Step 3: 2FA Password -> Auto move to Set Ad
    elif step == "WAIT_2FA":
        client = state["client"]
        try:
            await client.sign_in(password=msg_text)
            session_str = client.session.save()
            await client.disconnect()
            config["session"] = session_str
            save_data(config)

            user_states[uid] = {"step": "WAIT_AD", "auto_flow": True}
            await update.message.reply_text(
                "2FA verified! Account login ho gaya.\n\n"
                f"<b>Session Backup:</b> <code>{html.escape(session_str)}</code>\n\n"
                "Ab apna <b>Ad Message</b> bhejein:",
                parse_mode="HTML",
            )
        except Exception as e:
            try:
                await client.disconnect()
            except Exception:
                pass
            user_states.pop(uid, None)
            await update.message.reply_text(
                f"Galat password: {html.escape(str(e))}\nDobara /start bhejein.",
                parse_mode="HTML",
            )

    # Step 4: Set Ad -> Reset counter to 1 & Auto move to Set Delay
    elif step == "WAIT_AD":
        config["ad_text"] = msg_text
        config["ad_counter"] = 1
        save_data(config)
        sample = format_human_ad(msg_text)

        if state.get("auto_flow"):
            user_states[uid] = {"step": "WAIT_DELAY", "auto_flow": True}
            await update.message.reply_text(
                f"Ad save ho gaya!\n<b>Preview (1st Ad):</b>\n{sample}\n\n"
                f"Ab har round ke beech ka <b>Time Delay (seconds mein)</b> bhejein (jaise <code>60</code> ya <code>120</code>):",
                parse_mode="HTML",
            )
        else:
            user_states.pop(uid, None)
            text, keyboard = get_main_menu()
            await update.message.reply_text(
                f"Ad save ho gaya!\n<b>Preview (1st Ad):</b>\n{sample}\n\n{text}",
                reply_markup=keyboard,
                parse_mode="HTML",
            )

    # Step 5: Set Delay -> Auto Start Bot
    elif step == "WAIT_DELAY":
        if not msg_text.isdigit() or int(msg_text) < 10:
            await update.message.reply_text("Kam se kam 10 seconds ka valid number bhejein:")
            return

        config["delay"] = int(msg_text)
        auto_flow = state.get("auto_flow", False)
        user_states.pop(uid, None)

        if auto_flow and config.get("session") and config.get("ad_text"):
            config["running"] = True
            save_data(config)
            start_ad_loop()
            await asyncio.sleep(0.5)
            text, keyboard = get_main_menu()
            await update.message.reply_text(
                f"Delay {msg_text}s set ho gaya aur <b>Bot automatic start ho gaya hai!</b>\n\n{text}",
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        else:
            save_data(config)
            text, keyboard = get_main_menu()
            await update.message.reply_text(
                f"Delay {msg_text} seconds set ho gaya!\n\n{text}",
                reply_markup=keyboard,
                parse_mode="HTML",
            )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, (Conflict, NetworkError, TimedOut)):
        return
    print(f"Handled runtime warning: {err}")


async def health_check(request):
    return web.json_response(
        {
            "status": "alive",
            "bot_running": config.get("running", False),
            "next_ad_number": config.get("ad_counter", 1),
            "rounds_completed": stats["rounds_completed"],
            "total_sent": stats["total_sent"],
            "current_state": stats["last_status"],
        }
    )


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


def build_httpx_request():
    transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=3)
    return HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
        httpx_kwargs={"transport": transport},
    )


async def main():
    global bot_app
    await start_web_server()
    print("Web server bound successfully on port", os.environ.get("PORT", 10000))

    while True:
        try:
            application = (
                Application.builder()
                .token(BOT_TOKEN)
                .request(build_httpx_request())
                .get_updates_request(build_httpx_request())
                .build()
            )
            application.add_handler(CommandHandler("start", start_handler))
            application.add_handler(CallbackQueryHandler(callback_handler))
            application.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler)
            )
            application.add_error_handler(error_handler)

            await application.initialize()
            await application.start()
            await application.updater.start_polling(
                drop_pending_updates=True,
                timeout=20,
                bootstrap_retries=-1,
            )

            bot_app = application
            print("Controller Bot successfully started and connected!")

            if config.get("running") and config.get("session"):
                start_ad_loop()

            await asyncio.Event().wait()

        except Exception as e:
            print(f"Retrying connection in 5s due to: {e}")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())

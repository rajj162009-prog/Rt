import os
import sys
import html
import json
import random
import string
import asyncio
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
from telethon.errors import SessionPasswordNeededError, FloodWaitError

# Environment Variables
raw_api_id = os.environ.get("API_ID", "").strip()
API_HASH = os.environ.get("API_HASH", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
raw_owner_id = os.environ.get("OWNER_ID", "0").strip()

if not raw_api_id.isdigit() or not API_HASH or not BOT_TOKEN:
    print("ERROR: API_ID, API_HASH, aur BOT_TOKEN environment variables check karein!")
    sys.exit(1)

API_ID = int(raw_api_id)
OWNER_ID = int(raw_owner_id) if raw_owner_id.isdigit() else 0

DATA_FILE = "bot_data.json"
SPECIAL_CHARS = "-/:(()₹&@“.’"


def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "session": os.environ.get("SESSION_STRING", "").strip(),
        "ad_text": "",
        "delay": 120,
        "running": False,
    }


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


config = load_data()
user_states = {}
ad_task = None


def create_telethon_client(session_str=""):
    return TelegramClient(
        StringSession(session_str),
        API_ID,
        API_HASH,
        device_model="Samsung Galaxy S23",
        system_version="Android 14",
        app_version="10.14.5",
    )


def generate_noise():
    length = random.randint(3, 6)
    pool = SPECIAL_CHARS + string.digits
    return "".join(random.choice(pool) for _ in range(length))


def format_human_ad(base_text):
    prefix = generate_noise()
    suffix = generate_noise()
    return f"{prefix} {base_text} {suffix}"


def get_main_menu():
    acc_status = "Logged In" if config.get("session") else "Not Logged In"
    ad_status = "Set" if config.get("ad_text") else "Not Set"
    run_status = "Running" if config.get("running") else "Stopped"

    text = (
        f"<b>Ad Bot Control Panel</b>\n\n"
        f"• <b>Account:</b> {acc_status}\n"
        f"• <b>Ad Text:</b> {ad_status}\n"
        f"• <b>Round Delay:</b> {config.get('delay')} sec\n"
        f"• <b>Status:</b> {run_status}"
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
        ]
    )
    return text, keyboard


def cancel_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Cancel", callback_data="cancel")]]
    )


async def ad_worker():
    global config
    if not config.get("session"):
        config["running"] = False
        save_data(config)
        return

    user_client = create_telethon_client(config["session"])
    await user_client.connect()

    if not await user_client.is_user_authorized():
        config["running"] = False
        config["session"] = ""
        save_data(config)
        await user_client.disconnect()
        return

    try:
        while config.get("running"):
            ad_text = config.get("ad_text", "").strip()
            if not ad_text:
                await asyncio.sleep(5)
                continue

            async for dialog in user_client.iter_dialogs():
                if not config.get("running"):
                    break
                if dialog.is_group:
                    try:
                        # Human-like typing indicator
                        async with user_client.action(dialog.id, "typing"):
                            await asyncio.sleep(random.uniform(1.5, 3.5))

                        final_msg = format_human_ad(ad_text)
                        await user_client.send_message(dialog.id, final_msg)

                        # Natural pause between groups
                        await asyncio.sleep(random.uniform(3.5, 7.5))

                    except FloodWaitError as e:
                        await asyncio.sleep(e.seconds + 5)
                    except Exception:
                        continue

            base_delay = int(config.get("delay", 120))
            jitter = random.randint(5, 25)
            total_sleep = base_delay + jitter

            for _ in range(total_sleep):
                if not config.get("running"):
                    break
                await asyncio.sleep(1)
    finally:
        await user_client.disconnect()


def start_ad_loop():
    global ad_task
    if ad_task is None or ad_task.done():
        ad_task = asyncio.create_task(ad_worker())


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if OWNER_ID and uid != OWNER_ID:
        return

    user_states.pop(uid, None)
    text, keyboard = get_main_menu()
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
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
            "Apna Ad message bhejein (is text ke aage aur peeche automatic special characters + numbers lag jayenge):",
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
            await query.answer("Pehle Account Login karein!", show_alert=True)
            return
        if not config.get("ad_text"):
            await query.answer("Pehle Ad Message set karein!", show_alert=True)
            return
        if config.get("running"):
            await query.answer("Bot pehle se chal raha hai!", show_alert=True)
            return

        await query.answer("Bot start ho gaya!")
        config["running"] = True
        save_data(config)
        start_ad_loop()
        text, keyboard = get_main_menu()
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")

    elif data == "stop_bot":
        await query.answer("Bot stop ho gaya!")
        config["running"] = False
        save_data(config)
        text, keyboard = get_main_menu()
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")

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
        await client.connect()
        try:
            sent = await client.send_code_request(msg_text)
            user_states[uid] = {
                "step": "WAIT_CODE",
                "phone": msg_text,
                "hash": sent.phone_code_hash,
                "client": client,
            }
            await update.message.reply_text(
                "Telegram par OTP aaya hoga. <b>Spaces ke saath</b> OTP bhejein (jaise <code>1 2 3 4 5</code>):",
                parse_mode="HTML",
            )
        except Exception as e:
            await client.disconnect()
            user_states.pop(uid, None)
            await update.message.reply_text(
                f"Error: {html.escape(str(e))}\nDobara /start bhejein.",
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
                "Account successfully login ho gaya!\n\nAb apna <b>Ad Message</b> bhejein:",
                parse_mode="HTML",
            )

        except SessionPasswordNeededError:
            state["step"] = "WAIT_2FA"
            await update.message.reply_text(
                "Is account par 2-Step Verification (Password) laga hai. Apna password bhejein:"
            )
        except Exception as e:
            await client.disconnect()
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
                "2FA verified! Account login ho gaya.\n\nAb apna <b>Ad Message</b> bhejein:",
                parse_mode="HTML",
            )
        except Exception as e:
            await client.disconnect()
            user_states.pop(uid, None)
            await update.message.reply_text(
                f"Galat password: {html.escape(str(e))}\nDobara /start bhejein.",
                parse_mode="HTML",
            )

    # Step 4: Set Ad -> Auto move to Set Delay (if in setup flow)
    elif step == "WAIT_AD":
        config["ad_text"] = msg_text
        save_data(config)
        sample = format_human_ad(msg_text)

        if state.get("auto_flow"):
            user_states[uid] = {"step": "WAIT_DELAY", "auto_flow": True}
            await update.message.reply_text(
                f"Ad save ho gaya!\n<b>Preview:</b> <code>{html.escape(sample)}</code>\n\n"
                f"Ab har round ke beech ka <b>Time Delay (seconds mein)</b> bhejein (jaise <code>60</code> ya <code>120</code>):",
                parse_mode="HTML",
            )
        else:
            user_states.pop(uid, None)
            text, keyboard = get_main_menu()
            await update.message.reply_text(
                f"Ad save ho gaya!\n<b>Preview:</b> <code>{html.escape(sample)}</code>\n\n{text}",
                reply_markup=keyboard,
                parse_mode="HTML",
            )

    # Step 5: Set Delay -> Auto Start Bot (if in setup flow)
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


async def health_check(request):
    return web.Response(text="Ad Bot is alive!")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


async def main():
    await start_web_server()

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)

    print("Controller Bot & Web Server successfully started!")

    if config.get("running") and config.get("session"):
        start_ad_loop()

    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())

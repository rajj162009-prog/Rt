import os
import json
import random
import string
import asyncio
from aiohttp import web
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, FloodWaitError

# Environment Variables (Render Dashboard mein set karne hain)
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("OWNER_ID", 0))  # Apni Telegram User ID daalein

DATA_FILE = "bot_data.json"
SPECIAL_CHARS = "-/:(()₹&@“.’"

# Data load/save helper
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "session": "",
        "ad_text": "",
        "delay": 120,
        "running": False
    }

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

config = load_data()
user_states = {}
ad_task = None

# Random special characters + numbers generator
def generate_noise():
    length = random.randint(3, 6)
    pool = SPECIAL_CHARS + string.digits
    return "".join(random.choice(pool) for _ in range(length))

def format_human_ad(base_text):
    prefix = generate_noise()
    suffix = generate_noise()
    return f"{prefix} {base_text} {suffix}"

# Controller Bot Client
bot = TelegramClient("controller_bot", API_ID, API_HASH)

def get_main_menu():
    acc_status = "Logged In" if config.get("session") else "Not Logged In"
    ad_status = "Set" if config.get("ad_text") else "Not Set"
    run_status = "Running" if config.get("running") else "Stopped"

    text = (
        f"**Ad Bot Control Panel**\n\n"
        f"• **Account:** {acc_status}\n"
        f"• **Ad Text:** {ad_status}\n"
        f"• **Round Delay:** {config.get('delay')} sec\n"
        f"• **Status:** {run_status}"
    )
    buttons = [
        [Button.inline("1. Account Login", b"login_acc")],
        [Button.inline("2. Set Ad Message", b"set_ad")],
        [Button.inline("3. Set Time Delay", b"set_delay")],
        [
            Button.inline("Start Bot", b"start_bot"),
            Button.inline("Stop Bot", b"stop_bot")
        ]
    ]
    return text, buttons

# Background Ad Sender Loop
async def ad_worker():
    global config
    if not config.get("session"):
        config["running"] = False
        save_data(config)
        return

    user_client = TelegramClient(StringSession(config["session"]), API_ID, API_HASH)
    await user_client.connect()

    if not await user_client.is_user_authorized():
        config["running"] = False
        config["session"] = ""
        save_data(config)
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
                        # Human-like typing action
                        async with user_client.action(dialog.id, "typing"):
                            await asyncio.sleep(random.uniform(1.5, 3.5))

                        final_msg = format_human_ad(ad_text)
                        await user_client.send_message(dialog.id, final_msg)

                        # Har group ke beech natural pause
                        await asyncio.sleep(random.uniform(3.0, 7.0))

                    except FloodWaitError as e:
                        await asyncio.sleep(e.seconds + 5)
                    except Exception:
                        continue

            # Ek round poora hone ke baad set delay + random human jitter
            base_delay = int(config.get("delay", 120))
            jitter = random.randint(5, 25)
            total_sleep = base_delay + jitter

            for _ in range(total_sleep):
                if not config.get("running"):
                    break
                await asyncio.sleep(1)
    finally:
        await user_client.disconnect()

# Start Command
@bot.on(events.NewMessage(pattern="/start"))
async def start_handler(event):
    if OWNER_ID and event.sender_id != OWNER_ID:
        return
    user_states.pop(event.sender_id, None)
    text, buttons = get_main_menu()
    await event.respond(text, buttons=buttons)

# Button Callbacks
@bot.on(events.CallbackQuery)
async def callback_handler(event):
    global ad_task
    uid = event.sender_id
    if OWNER_ID and uid != OWNER_ID:
        return

    data = event.data.decode("utf-8")

    if data == "login_acc":
        user_states[uid] = {"step": "WAIT_PHONE"}
        await event.edit(
            "Apna Telegram phone number country code ke saath bhejein (jaise `+919876543210`):",
            buttons=[[Button.inline("Cancel", b"cancel")]]
        )

    elif data == "set_ad":
        user_states[uid] = {"step": "WAIT_AD"}
        await event.edit(
            "Apna Ad message bhejein (is text ke aage aur peeche automatic special characters + numbers lag jayenge):",
            buttons=[[Button.inline("Cancel", b"cancel")]]
        )

    elif data == "set_delay":
        user_states[uid] = {"step": "WAIT_DELAY"}
        await event.edit(
            "Har round ke beech kitne seconds ka delay rakhna hai? Number bhejein (jaise `60` ya `300`):",
            buttons=[[Button.inline("Cancel", b"cancel")]]
        )

    elif data == "start_bot":
        if not config.get("session"):
            await event.answer("Pehle Account Login karein!", alert=True)
            return
        if not config.get("ad_text"):
            await event.answer("Pehle Ad Message set karein!", alert=True)
            return
        if config.get("running"):
            await event.answer("Bot pehle se chal raha hai!", alert=True)
            return

        config["running"] = True
        save_data(config)
        ad_task = asyncio.create_task(ad_worker())
        text, buttons = get_main_menu()
        await event.edit(text, buttons=buttons)

    elif data == "stop_bot":
        config["running"] = False
        save_data(config)
        text, buttons = get_main_menu()
        await event.edit(text, buttons=buttons)

    elif data == "cancel":
        user_states.pop(uid, None)
        text, buttons = get_main_menu()
        await event.edit(text, buttons=buttons)

# Conversation Steps Handler
@bot.on(events.NewMessage)
async def message_handler(event):
    uid = event.sender_id
    if OWNER_ID and uid != OWNER_ID:
        return
    if event.text and event.text.startswith("/"):
        return

    state = user_states.get(uid)
    if not state:
        return

    step = state.get("step")
    msg_text = event.text.strip()

    # Step 1: Phone Number
    if step == "WAIT_PHONE":
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        try:
            sent = await client.send_code_request(msg_text)
            user_states[uid] = {
                "step": "WAIT_CODE",
                "phone": msg_text,
                "hash": sent.phone_code_hash,
                "client": client
            }
            await event.respond(
                "Telegram par OTP aaya hoga. **Spaces ke saath** OTP bhejein (jaise `1 2 3 4 5`), warna Telegram code block kar deta hai:"
            )
        except Exception as e:
            await client.disconnect()
            user_states.pop(uid, None)
            await event.respond(f"Error: {e}\nDobara /start bhejein.")

    # Step 2: OTP Code
    elif step == "WAIT_CODE":
        code = msg_text.replace(" ", "").replace("-", "")
        client = state["client"]
        try:
            await client.sign_in(
                phone=state["phone"],
                code=code,
                phone_code_hash=state["hash"]
            )
            session_str = client.session.save()
            await client.disconnect()
            config["session"] = session_str
            save_data(config)
            user_states.pop(uid, None)

            text, buttons = get_main_menu()
            await event.respond("Account successfully login ho gaya!", buttons=buttons)

        except SessionPasswordNeededError:
            state["step"] = "WAIT_2FA"
            await event.respond("Is account par 2-Step Verification (Password) laga hai. Apna password bhejein:")
        except Exception as e:
            await client.disconnect()
            user_states.pop(uid, None)
            await event.respond(f"Login failed: {e}\nDobara /start bhejein.")

    # Step 3: 2FA Password
    elif step == "WAIT_2FA":
        client = state["client"]
        try:
            await client.sign_in(password=msg_text)
            session_str = client.session.save()
            await client.disconnect()
            config["session"] = session_str
            save_data(config)
            user_states.pop(uid, None)

            text, buttons = get_main_menu()
            await event.respond("2FA verified! Account login ho gaya.", buttons=buttons)
        except Exception as e:
            await client.disconnect()
            user_states.pop(uid, None)
            await event.respond(f"Galat password: {e}\nDobara /start bhejein.")

    # Step 4: Save Ad Message
    elif step == "WAIT_AD":
        config["ad_text"] = msg_text
        save_data(config)
        user_states.pop(uid, None)
        sample = format_human_ad(msg_text)
        text, buttons = get_main_menu()
        await event.respond(f"Ad save ho gaya!\n\n**Preview:**\n`{sample}`", buttons=buttons)

    # Step 5: Save Time Delay
    elif step == "WAIT_DELAY":
        if not msg_text.isdigit() or int(msg_text) < 10:
            await event.respond("Kam se kam 10 seconds ka valid number bhejein:")
            return
        config["delay"] = int(msg_text)
        save_data(config)
        user_states.pop(uid, None)
        text, buttons = get_main_menu()
        await event.respond(f"Delay {msg_text} seconds set ho gaya!", buttons=buttons)

# Render Port Binding Dummy Server
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
    global ad_task
    await start_web_server()
    await bot.start(bot_token=BOT_TOKEN)
    if config.get("running") and config.get("session"):
        ad_task = asyncio.create_task(ad_worker())
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())

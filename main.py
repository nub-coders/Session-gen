"""Session string generator bot for Pyrogram and Telethon."""
import logging

from pyrogram import Client, filters
from pyrogram.enums import ButtonStyle as S
from pyrogram.errors import MessageNotModified
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import API_ID, API_HASH, BOT_TOKEN, GROUP, CHANNEL
from flavors import Pyro, Tele, flood_seconds

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger(__name__)

bot = Client("sessionbot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)

# One active generation flow per user; keyed by user id, stores the logged-in client.
states: dict[int, dict] = {}


async def _edit(message: Message, text: str, **kwargs):
    try:
        await message.edit_text(text, **kwargs)
    except MessageNotModified:
        pass


async def _drop(user_id: int):
    """End the flow: forget state and disconnect any half-authed client."""
    state = states.pop(user_id, None)
    if state and state.get("client"):
        try:
            await state["client"].disconnect()
        except Exception:
            pass


async def _fail(user_id: int, status: Message, text: str):
    await _drop(user_id)
    await _edit(status, text)


def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="gen_cancel", style=S.DANGER)]])


def _choice_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Pyrogram", callback_data="gen_choose:pyro", style=S.PRIMARY),
            InlineKeyboardButton("Telethon", callback_data="gen_choose:tele", style=S.SUCCESS),
        ]
    ])


# ── Flow steps ──

@bot.on_callback_query(filters.regex("^gen_cancel$"))
async def cb_cancel(_, cq: CallbackQuery):
    await _drop(cq.from_user.id)
    await cq.answer("Cancelled.", show_alert=False)
    await _edit(cq.message, "❌ Generation cancelled. Send /gen to start again.")


@bot.on_callback_query(filters.regex("^gen_choose:"))
async def cb_choose(_, cq: CallbackQuery):
    user_id = cq.from_user.id
    flavor = Tele if cq.data == "gen_choose:tele" else Pyro
    states[user_id] = {"step": "phone", "flavor": flavor}
    await cq.answer(f"{flavor.name} selected")
    await _edit(
        cq.message,
        f"📱 **{flavor.name}** selected.\n\n"
        "Please enter your phone number in international format (e.g., +1234567890)",
        reply_markup=_back_kb(),
    )


@bot.on_message(filters.text & filters.private & ~filters.regex(r"^/"))
async def on_text(_, message: Message):
    state = states.get(message.from_user.id)
    if not state:
        return
    text = message.text.strip()

    if state["step"] == "phone":
        await _handle_phone(message, text, state)
    elif state["step"] == "code":
        await _handle_code(message, text, state)
    elif state["step"] == "password":
        await _handle_password(message, text, state)


async def _handle_phone(message: Message, phone: str, state: dict):
    if not phone.startswith("+"):
        phone = "+" + phone
    flavor = state["flavor"]

    if not 5 <= len(phone) <= 18:
        await message.reply("❌ Invalid phone number. Send it in international format (e.g., +1234567890)")
        return

    status = await message.reply(f"⏳ Sending code to `{phone}`...")
    try:
        # Stash the client before connecting so any failure below still gets it disconnected.
        state["client"] = client = flavor.client(message.from_user.id)
        await client.connect()
        phone_code_hash = await flavor.send_code(client, phone)
        state.update({"step": "code", "phone": phone, "phone_code_hash": phone_code_hash})
        await _edit(status, f"✅ Code sent to `{phone}`\n\nEnter the verification code:\n\nAdd spaces between digits (e.g. `1 2 3 4 5`)", reply_markup=_back_kb())
    except flavor.phone_invalid:
        await _fail(message.from_user.id, status, "❌ Invalid phone number. Send /gen to try again.")
    except flavor.flood as e:
        await _fail(message.from_user.id, status, f"⚠️ Too many requests. Try again in {flood_seconds(e)} seconds.")
    except Exception as e:
        log.exception("phone step failed for %s", message.from_user.id)
        await _fail(message.from_user.id, status, f"❌ Error: `{e}`")


async def _handle_code(message: Message, code: str, state: dict):
    flavor = state["flavor"]
    clean_code = code.replace(" ", "").replace(".", "").replace("-", "")
    status = await message.reply("⏳ Verifying code...")

    try:
        await flavor.sign_in(state["client"], state["phone"], state["phone_code_hash"], clean_code)
    except flavor.need_password:
        state["step"] = "password"
        await _edit(status, "🔐 This account has 2FA enabled. Enter the 2FA password:", reply_markup=_back_kb())
        return
    except flavor.code_invalid:
        await _edit(status, "❌ Invalid code. Try again (e.g. `1 2 3 4 5`).", reply_markup=_back_kb())
        return
    except flavor.code_expired:
        await _fail(message.from_user.id, status, "❌ Code expired. Send /gen to start again.")
        return
    except Exception as e:
        log.exception("code step failed for %s", message.from_user.id)
        await _fail(message.from_user.id, status, f"❌ Error: `{e}`")
        return

    await _finish(message, status, state)


async def _handle_password(message: Message, password: str, state: dict):
    flavor = state["flavor"]
    status = await message.reply("⏳ Checking password...")

    try:
        await flavor.check_password(state["client"], state["phone"], password.strip())
    except flavor.password_invalid:
        await _edit(status, "❌ Wrong password. Try again:", reply_markup=_back_kb())
        return
    except Exception as e:
        log.exception("password step failed for %s", message.from_user.id)
        await _fail(message.from_user.id, status, f"❌ Error: `{e}`")
        return

    await _finish(message, status, state)


async def _finish(message: Message, status: Message, state: dict):
    flavor = state["flavor"]
    client = state["client"]
    try:
        for chat in (GROUP, CHANNEL):
            try:
                await flavor.join(client, chat)
            except Exception:
                pass
        string_session = await flavor.export(client)
    except Exception as e:
        log.exception("session export failed for %s", message.from_user.id)
        await _fail(message.from_user.id, status, f"❌ Error: `{e}`")
        return

    await client.disconnect()
    states.pop(message.from_user.id, None)

    me = await bot.get_me()
    await _edit(
        status,
        f"🎉 **Success!** Here is your {flavor.name} session string:\n\n`{string_session}`\n\n"
        f"Generated by @{me.username}\n\n"
        "⚠️ **Important**: Keep this string secret and never share it with anyone!",
    )


# ── Static handlers ──

@bot.on_message(filters.command("gen") & filters.private)
async def start_gen(_, message: Message):
    await _drop(message.from_user.id)  # restarting mid-flow must not leak the old client
    await message.reply(
        "🧩 **Which session string do you want?**\n\n"
        "Choose the library you plan to use the session with:",
        reply_markup=_choice_kb(),
    )


@bot.on_message(filters.command("start") & filters.private)
async def start_handler(_, message: Message):
    await message.reply(
        "🤖 **Welcome to Session String Generator Bot!**\n\n"
        "I can generate **Pyrogram** and **Telethon** session strings safely and securely.\n\n"
        "**Available Commands:**\n"
        "• `/start` - Show this welcome message\n"
        "• `/gen` - Generate a session string (choose Pyrogram or Telethon)\n\n"
        "**How to use:**\n"
        "1. Send /gen\n"
        "2. Choose the library\n"
        "3. Enter your phone number\n"
        "4. Enter the verification code\n"
        "5. If 2FA is enabled, enter your password\n"
        "6. Get your session string!\n\n"
        "⚠️ **Important:** Never share your session string with anyone! It provides full access to your Telegram account.",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Support Group 💭", url="https://t.me/nub_coder_s"),
                InlineKeyboardButton("Updates Channel 📢", url="https://t.me/nub_coders"),
            ],
            [
                InlineKeyboardButton("🤖 ᴏᴜʀ ʙᴏᴛs", url="https://t.me/+FbIuEWrOYlEwYzM1", style=S.PRIMARY),
            ],
        ]),
        disable_web_page_preview=True,
    )


if __name__ == "__main__":
    log.info("Starting bot...")
    bot.run()

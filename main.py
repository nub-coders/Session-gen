"""Session string generator bot for Pyrogram and Telethon with Kurigram Bot API 10.2 UI."""
import logging

from pyrogram import Client, filters
from pyrogram.enums import ButtonStyle as S
from pyrogram.errors import MessageNotModified
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputRichMessage,
    Message,
)

from config import API_ID, API_HASH, BOT_TOKEN, GROUP, CHANNEL
from flavors import Pyro, Tele, flood_seconds

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger(__name__)

bot = Client("sessionbot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)

# One active generation flow per user; keyed by user id, stores the logged-in client.
states: dict[int, dict] = {}


# ── UI Helpers ──

async def _send_rich(
    chat_id: int | str,
    rich_html: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message:
    """Send a native Rich Message with HTML formatting."""
    return await bot.send_rich_message(
        chat_id=chat_id,
        rich_message=InputRichMessage(html=rich_html),
        reply_markup=reply_markup,
    )


async def _edit(
    message: Message,
    rich_html: str,
    reply_markup: InlineKeyboardMarkup | None = None,
):
    """Edit an existing message using native Rich HTML."""
    try:
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=message.id,
            rich_message=InputRichMessage(html=rich_html),
            reply_markup=reply_markup,
        )
    except MessageNotModified:
        pass


async def _draft(chat_id: int | str, draft_id: int, rich_html: str):
    """Stream a live rich draft update to the user."""
    try:
        await bot.send_rich_message_draft(
            chat_id=chat_id,
            draft_id=draft_id,
            rich_message=InputRichMessage(html=rich_html),
        )
    except Exception:
        pass


async def _drop(user_id: int):
    """End the flow: forget state and disconnect any half-authed client."""
    state = states.pop(user_id, None)
    if state and state.get("client"):
        try:
            await state["client"].disconnect()
        except Exception:
            pass


async def _fail(user_id: int, status: Message, rich_html: str, reply_markup: InlineKeyboardMarkup | None = None):
    """Clean up state and display a formatted failure rich message."""
    await _drop(user_id)
    if reply_markup is None:
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Try Again", callback_data="gen_start", style=S.PRIMARY)]
        ])
    await _edit(status, rich_html, reply_markup=reply_markup)


def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="gen_cancel", style=S.DANGER)]
    ])


def _choice_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Pyrogram", callback_data="gen_choose:pyro", style=S.PRIMARY),
            InlineKeyboardButton("Telethon", callback_data="gen_choose:tele", style=S.SUCCESS),
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="gen_cancel", style=S.DANGER),
        ],
    ])


def _choice_html() -> str:
    return (
        "<h2>🧩 Choose Client Framework</h2>\n"
        "<p>Select the library you plan to use with your generated string session:</p>\n"
        "<table border=\"1\">\n"
        "  <tr>\n"
        "    <td><b>Framework</b></td>\n"
        "    <td><b>Description</b></td>\n"
        "  </tr>\n"
        "  <tr>\n"
        "    <td><b>Pyrogram</b></td>\n"
        "    <td>Modern async MTProto framework for bots & userbots</td>\n"
        "  </tr>\n"
        "  <tr>\n"
        "    <td><b>Telethon</b></td>\n"
        "    <td>Classic asyncio Telegram client library</td>\n"
        "  </tr>\n"
        "</table>\n\n"
        "<blockquote>Select a framework below to continue:</blockquote>"
    )


# ── Flow callbacks & handlers ──

@bot.on_callback_query(filters.regex("^gen_cancel$"))
async def cb_cancel(_, cq: CallbackQuery):
    await _drop(cq.from_user.id)
    await cq.answer("Generation cancelled.", show_alert=False)
    await _edit(
        cq.message,
        "<h2>❌ Generation Cancelled</h2>\n"
        "<p>Your session setup has been aborted and cleared from memory.</p>\n"
        "<blockquote>Send <code>/gen</code> or click below to start over anytime.</blockquote>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Start Again", callback_data="gen_start", style=S.PRIMARY)]
        ]),
    )


@bot.on_callback_query(filters.regex("^gen_start$"))
async def cb_start_gen(_, cq: CallbackQuery):
    await _drop(cq.from_user.id)
    await cq.answer()
    await _edit(cq.message, _choice_html(), reply_markup=_choice_kb())


@bot.on_callback_query(filters.regex("^gen_choose:"))
async def cb_choose(_, cq: CallbackQuery):
    user_id = cq.from_user.id
    flavor = Tele if cq.data == "gen_choose:tele" else Pyro
    states[user_id] = {"step": "phone", "flavor": flavor}
    await cq.answer(f"Selected {flavor.name}", show_alert=False)

    await _edit(
        cq.message,
        f"<h2>📱 {flavor.name} Session Setup</h2>\n"
        "<p>Please enter your phone number in <b>international format</b>.</p>\n"
        "<table border=\"1\">\n"
        "  <tr>\n"
        "    <td><b>Parameter</b></td>\n"
        "    <td><b>Value</b></td>\n"
        "  </tr>\n"
        "  <tr>\n"
        "    <td><b>Framework</b></td>\n"
        f"    <td><b>{flavor.name}</b></td>\n"
        "  </tr>\n"
        "  <tr>\n"
        "    <td><b>Format Example</b></td>\n"
        "    <td><code>+1234567890</code></td>\n"
        "  </tr>\n"
        "</table>\n\n"
        "<details>\n"
        "  <summary>ℹ️ Why is my phone number required?</summary>\n"
        "  <p>Telegram requires your phone number to authenticate your account and dispatch an official verification code.</p>\n"
        "</details>\n\n"
        "<blockquote>Send your phone number as a message (e.g. <code>+1234567890</code>):</blockquote>",
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
        await _send_rich(
            message.chat.id,
            "<h2>❌ Invalid Phone Number</h2>\n"
            "<blockquote>Please send your phone number in international format with country code (e.g. <code>+1234567890</code>).</blockquote>",
            reply_markup=_back_kb(),
        )
        return

    draft_id = bot.rnd_id()
    await _draft(message.chat.id, draft_id, "<h3>⏳ Connecting to Telegram servers...</h3>")

    status = await _send_rich(
        message.chat.id,
        f"<h3>⏳ Requesting verification code for <code>{phone}</code>...</h3>",
    )

    try:
        # Stash the client before connecting so any failure below still gets it disconnected.
        state["client"] = client = flavor.client(message.from_user.id)
        await client.connect()
        await _draft(message.chat.id, draft_id, f"<h3>📨 Sending code to <code>{phone}</code>...</h3>")
        phone_code_hash = await flavor.send_code(client, phone)
        state.update({"step": "code", "phone": phone, "phone_code_hash": phone_code_hash})

        await _edit(
            status,
            f"<h2>✅ Verification Code Sent</h2>\n"
            f"<p>An official authorization code has been sent to your Telegram app for <code>{phone}</code>.</p>\n"
            "<table border=\"1\">\n"
            "  <tr>\n"
            "    <td><b>Target Phone</b></td>\n"
            f"    <td><code>{phone}</code></td>\n"
            "  </tr>\n"
            "  <tr>\n"
            "    <td><b>Framework</b></td>\n"
            f"    <td><b>{flavor.name}</b></td>\n"
            "  </tr>\n"
            "  <tr>\n"
            "    <td><b>Status</b></td>\n"
            "    <td><b>Awaiting Code</b></td>\n"
            "  </tr>\n"
            "</table>\n\n"
            "<details>\n"
            "  <summary>💡 Pro Tip: Entering Verification Code</summary>\n"
            "  <p>Separate digits with spaces (e.g. <code>1 2 3 4 5</code>) to avoid Telegram auto-login interference.</p>\n"
            "</details>\n\n"
            "<blockquote>Please send the verification code below:</blockquote>",
            reply_markup=_back_kb(),
        )
    except flavor.phone_invalid:
        await _fail(
            message.from_user.id,
            status,
            "<h2>❌ Invalid Phone Number</h2>\n"
            "<blockquote>The phone number entered is not valid or not registered on Telegram.</blockquote>",
        )
    except flavor.flood as e:
        secs = flood_seconds(e)
        await _fail(
            message.from_user.id,
            status,
            "<h2>⚠️ Rate Limited</h2>\n"
            f"<blockquote>Too many attempts. Telegram requires you to wait <b>{secs}</b> seconds before trying again.</blockquote>",
        )
    except Exception as e:
        log.exception("phone step failed for %s", message.from_user.id)
        await _fail(
            message.from_user.id,
            status,
            "<h2>❌ Connection Error</h2>\n"
            f"<blockquote>Failed to request verification code:<br/><code>{e}</code></blockquote>",
        )


async def _handle_code(message: Message, code: str, state: dict):
    flavor = state["flavor"]
    clean_code = code.replace(" ", "").replace(".", "").replace("-", "")

    draft_id = bot.rnd_id()
    await _draft(message.chat.id, draft_id, "<h3>⏳ Verifying authorization code...</h3>")

    status = await _send_rich(message.chat.id, "<h3>⏳ Verifying authorization code...</h3>")

    try:
        await flavor.sign_in(state["client"], state["phone"], state["phone_code_hash"], clean_code)
    except flavor.need_password:
        state["step"] = "password"
        await _edit(
            status,
            "<h2>🔐 Two-Factor Authentication Required</h2>\n"
            "<p>This Telegram account is protected with a <b>2FA Cloud Password</b>.</p>\n"
            "<table border=\"1\">\n"
            "  <tr>\n"
            "    <td><b>Account Phone</b></td>\n"
            f"    <td><code>{state.get('phone', 'N/A')}</code></td>\n"
            "  </tr>\n"
            "  <tr>\n"
            "    <td><b>Framework</b></td>\n"
            f"    <td><b>{flavor.name}</b></td>\n"
            "  </tr>\n"
            "  <tr>\n"
            "    <td><b>2FA Status</b></td>\n"
            "    <td><b>Enabled</b></td>\n"
            "  </tr>\n"
            "</table>\n\n"
            "<details>\n"
            "  <summary>🔒 Security & Privacy Notice</summary>\n"
            "  <p>Your password is processed strictly in-memory during authentication and is never logged, stored, or transferred.</p>\n"
            "</details>\n\n"
            "<blockquote>Please enter your 2FA password below:</blockquote>",
            reply_markup=_back_kb(),
        )
        return
    except flavor.code_invalid:
        await _edit(
            status,
            "<h2>❌ Invalid Verification Code</h2>\n"
            "<blockquote>The code you entered is incorrect. Please try again (e.g. <code>1 2 3 4 5</code>):</blockquote>",
            reply_markup=_back_kb(),
        )
        return
    except flavor.code_expired:
        await _fail(
            message.from_user.id,
            status,
            "<h2>❌ Verification Code Expired</h2>\n"
            "<blockquote>The verification code has expired. Please start again.</blockquote>",
        )
        return
    except Exception as e:
        log.exception("code step failed for %s", message.from_user.id)
        await _fail(
            message.from_user.id,
            status,
            "<h2>❌ Verification Error</h2>\n"
            f"<blockquote>An error occurred during verification:<br/><code>{e}</code></blockquote>",
        )
        return

    await _finish(message, status, state)


async def _handle_password(message: Message, password: str, state: dict):
    flavor = state["flavor"]

    draft_id = bot.rnd_id()
    await _draft(message.chat.id, draft_id, "<h3>⏳ Verifying 2FA password...</h3>")

    status = await _send_rich(message.chat.id, "<h3>⏳ Verifying 2FA password...</h3>")

    try:
        await flavor.check_password(state["client"], state["phone"], password.strip())
    except flavor.password_invalid:
        await _edit(
            status,
            "<h2>❌ Incorrect 2FA Password</h2>\n"
            "<blockquote>The 2FA password entered is incorrect. Please try again:</blockquote>",
            reply_markup=_back_kb(),
        )
        return
    except Exception as e:
        log.exception("password step failed for %s", message.from_user.id)
        await _fail(
            message.from_user.id,
            status,
            "<h2>❌ Password Verification Error</h2>\n"
            f"<blockquote>An error occurred during password verification:<br/><code>{e}</code></blockquote>",
        )
        return

    await _finish(message, status, state)


async def _finish(message: Message, status: Message, state: dict):
    flavor = state["flavor"]
    client = state["client"]
    draft_id = bot.rnd_id()

    try:
        await _draft(message.chat.id, draft_id, "<h3>⏳ Finalizing session authorization...</h3>")
        for chat in (GROUP, CHANNEL):
            try:
                await flavor.join(client, chat)
            except Exception:
                pass
        await _draft(message.chat.id, draft_id, f"<h3>⚡ Exporting {flavor.name} session string...</h3>")
        string_session = await flavor.export(client)
    except Exception as e:
        log.exception("session export failed for %s", message.from_user.id)
        await _fail(
            message.from_user.id,
            status,
            "<h2>❌ Export Error</h2>\n"
            f"<blockquote>Failed to export string session:<br/><code>{e}</code></blockquote>",
        )
        return

    await client.disconnect()
    states.pop(message.from_user.id, None)

    me = await bot.get_me()
    await _edit(
        status,
        f"<h1>🎉 Session Generated Successfully!</h1>\n"
        f"<p>Here is your <b>{flavor.name}</b> session string:</p>\n"
        "<table border=\"1\">\n"
        "  <tr>\n"
        "    <td><b>Framework</b></td>\n"
        f"    <td><b>{flavor.name}</b></td>\n"
        "  </tr>\n"
        "  <tr>\n"
        "    <td><b>Account ID</b></td>\n"
        f"    <td><code>{message.from_user.id}</code></td>\n"
        "  </tr>\n"
        "  <tr>\n"
        "    <td><b>Generated By</b></td>\n"
        f"    <td>@{me.username}</td>\n"
        "  </tr>\n"
        "</table>\n\n"
        "<h2>🔑 String Session</h2>\n"
        f"<pre><code>{string_session}</code></pre>\n\n"
        "<details>\n"
        "  <summary>🛡️ Critical Security Instructions</summary>\n"
        "  <p>• <b>Confidential:</b> Anyone with this string can access your Telegram account.<br/>\n"
        "  • <b>Revocation:</b> You can terminate this active session anytime via <b>Telegram Settings → Devices</b>.<br/>\n"
        "  • <b>Best Practice:</b> Never paste this string into public chats or insecure scripts.</p>\n"
        "</details>\n\n"
        "<blockquote>⚠️ <b>Important:</b> Keep this string session private and secure!</blockquote>",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Support Group 💭", url=f"https://t.me/{GROUP}", style=S.DEFAULT),
                InlineKeyboardButton("Updates Channel 📢", url=f"https://t.me/{CHANNEL}", style=S.DEFAULT),
            ],
            [
                InlineKeyboardButton("🔄 Generate Another", callback_data="gen_start", style=S.PRIMARY),
            ],
        ]),
    )


# ── Static handlers ──

@bot.on_message(filters.command("gen") & filters.private)
async def start_gen(_, message: Message):
    await _drop(message.from_user.id)  # restarting mid-flow must not leak the old client
    await _send_rich(
        message.chat.id,
        _choice_html(),
        reply_markup=_choice_kb(),
    )


@bot.on_message(filters.command("start") & filters.private)
async def start_handler(_, message: Message):
    await _send_rich(
        message.chat.id,
        "<h1>🤖 Session String Generator</h1>\n"
        "<p>Welcome! Generate <b>Pyrogram</b> and <b>Telethon</b> string sessions safely and securely.</p>\n"
        "<table border=\"1\">\n"
        "  <tr>\n"
        "    <td><b>Command</b></td>\n"
        "    <td><b>Description</b></td>\n"
        "  </tr>\n"
        "  <tr>\n"
        "    <td><code>/start</code></td>\n"
        "    <td>Show this welcome message & guide</td>\n"
        "  </tr>\n"
        "  <tr>\n"
        "    <td><code>/gen</code></td>\n"
        "    <td>Start session string generator flow</td>\n"
        "  </tr>\n"
        "</table>\n\n"
        "<details>\n"
        "  <summary>📖 How to Generate a Session</summary>\n"
        "  <p><b>1.</b> Send <code>/gen</code> to begin.<br/>\n"
        "  <b>2.</b> Choose your preferred framework (<b>Pyrogram</b> or <b>Telethon</b>).<br/>\n"
        "  <b>3.</b> Enter your phone number in international format.<br/>\n"
        "  <b>4.</b> Enter the official Telegram login code.<br/>\n"
        "  <b>5.</b> Enter your Two-Step Verification (2FA) password if enabled.<br/>\n"
        "  <b>6.</b> Instantly receive your session string!</p>\n"
        "</details>\n\n"
        "<blockquote>⚠️ <b>Security Notice:</b> Never share your session string with anyone! It provides full access to your Telegram account.</blockquote>",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Support Group 💭", url=f"https://t.me/{GROUP}", style=S.DEFAULT),
                InlineKeyboardButton("Updates Channel 📢", url=f"https://t.me/{CHANNEL}", style=S.DEFAULT),
            ],
            [
                InlineKeyboardButton("⚡ Start Generation", callback_data="gen_start", style=S.PRIMARY),
                InlineKeyboardButton("🤖 Our Bots", url="https://t.me/+FbIuEWrOYlEwYzM1", style=S.PRIMARY),
            ],
        ]),
    )


if __name__ == "__main__":
    log.info("Starting bot with Kurigram Bot API 10.2 UI...")
    bot.run()

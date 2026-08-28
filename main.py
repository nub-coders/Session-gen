"""Session string generator bot for Pyrogram and Telethon with Kurigram Bot API 10.2 UI & QR Login."""
import asyncio
import io
import logging

import qrcode
from pyrogram import Client, filters
from pyrogram.enums import ButtonStyle as S, ParseMode
from pyrogram.errors import MessageNotModified
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputRichMessage,
    Message,
)

from config import API_ID, API_HASH, BOT_TOKEN, GROUP, CHANNEL
from flavors import Pyro, Tele, flood_seconds

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger(__name__)

bot = Client("sessionbot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)

# One active generation flow per user; keyed by user id.
states: dict[int, dict] = {}
# Active QR background worker tasks; keyed by user id.
active_qr_tasks: dict[int, dict] = {}


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


def _make_qr_bio(url: str) -> io.BytesIO:
    """Render a tg:// login URL into a PNG image byte buffer."""
    img = qrcode.make(url)
    bio = io.BytesIO()
    bio.name = "qr.png"
    img.save(bio, "PNG")
    bio.seek(0)
    return bio


def _qr_caption(flavor_name: str) -> str:
    return (
        f"<b>⚡ {flavor_name} Instant QR Login</b>\n\n"
        "📱 <b>How to scan:</b>\n"
        "1. Open <b>Telegram</b> on your phone\n"
        "2. Go to <b>Settings ➔ Devices ➔ Link Desktop Device</b>\n"
        "3. Scan the QR code above\n\n"
        "🔄 <i>Auto-refreshes every 15 seconds.</i>"
    )


def _qr_kb(flavor_tag: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📞 Login with Phone Number", callback_data=f"gen_phone:{flavor_tag}", style=S.PRIMARY)],
        [InlineKeyboardButton("❌ Cancel", callback_data="gen_cancel", style=S.DANGER)],
    ])


async def _cancel_qr_task(user_id: int):
    """Cancel any running QR background tasks and disconnect its client."""
    entry = active_qr_tasks.pop(user_id, None)
    if entry:
        task = entry.get("task")
        if task and not task.done():
            task.cancel()
        client = entry.get("client")
        if client:
            try:
                if getattr(client, "is_connected", False):
                    await client.disconnect()
            except Exception:
                pass


async def _drop(user_id: int):
    """End the flow: cancel QR tasks, forget state, and disconnect any client."""
    await _cancel_qr_task(user_id)
    state = states.pop(user_id, None)
    if state and state.get("client"):
        try:
            if getattr(state["client"], "is_connected", False):
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
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("❌ Cancel", callback_data="gen_cancel", style=S.DANGER)]
        ]
    )


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
        "<blockquote>Select a framework below to generate your QR login instantly:</blockquote>"
    )


# ── QR Flow ──

async def _start_qr_flow(chat_id: int | str, user_id: int, flavor, flavor_tag: str, old_message: Message | None = None):
    """Initiate the QR code login flow, auto-refreshing media and listening for scans."""
    await _drop(user_id)

    loading_msg = None
    loading_html = f"<h3>⏳ Initializing {flavor.name} QR session...</h3>"
    if old_message:
        try:
            await _edit(old_message, loading_html)
            loading_msg = old_message
        except Exception:
            pass
    if not loading_msg:
        try:
            loading_msg = await _send_rich(chat_id, loading_html)
        except Exception:
            pass

    try:
        client = flavor.client(user_id)
        await client.connect()
        qr = await flavor.qr_init(client)
    except Exception as e:
        log.exception("Failed to initialize QR login for user %s: %s", user_id, e)
        err_html = (
            f"<h2>❌ Failed to initialize QR login</h2>\n"
            f"<blockquote>Error: <code>{e}</code></blockquote>"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📞 Login with Phone", callback_data=f"gen_phone:{flavor_tag}", style=S.PRIMARY)],
            [InlineKeyboardButton("🔄 Try Again", callback_data="gen_start", style=S.DEFAULT)],
        ])
        if loading_msg:
            await _edit(loading_msg, err_html, reply_markup=markup)
        else:
            await _send_rich(chat_id, err_html, reply_markup=markup)
        return

    bio = _make_qr_bio(qr.url)
    caption = _qr_caption(flavor.name)
    markup = _qr_kb(flavor_tag)

    if loading_msg:
        try:
            await loading_msg.delete()
        except Exception:
            pass

    try:
        photo_msg = await bot.send_photo(
            chat_id=chat_id,
            photo=bio,
            caption=caption,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        log.exception("Failed to send QR photo to %s: %s", user_id, e)
        try:
            await client.disconnect()
        except Exception:
            pass
        await _send_rich(
            chat_id,
            f"<h2>❌ Error Sending QR Code</h2>\n<blockquote>{e}</blockquote>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📞 Login with Phone", callback_data=f"gen_phone:{flavor_tag}", style=S.PRIMARY)],
                [InlineKeyboardButton("🔄 Try Again", callback_data="gen_start", style=S.DEFAULT)],
            ]),
        )
        return

    states[user_id] = {
        "step": "qr",
        "flavor": flavor,
        "flavor_tag": flavor_tag,
        "client": client,
        "photo_msg_id": photo_msg.id,
    }

    async def _worker():
        nonlocal photo_msg
        refresh_running = True

        async def _refresher():
            while refresh_running:
                await asyncio.sleep(15)
                try:
                    await flavor.qr_recreate(qr)
                    new_bio = _make_qr_bio(qr.url)
                    await bot.edit_message_media(
                        chat_id=chat_id,
                        message_id=photo_msg.id,
                        media=InputMediaPhoto(new_bio, caption=_qr_caption(flavor.name), parse_mode=ParseMode.HTML),
                        reply_markup=_qr_kb(flavor_tag),
                    )
                except asyncio.CancelledError:
                    break
                except Exception as re_err:
                    log.debug("QR refresh error: %s", re_err)

        refresher_task = asyncio.create_task(_refresher())

        try:
            try:
                await flavor.qr_wait(qr)
            except (asyncio.TimeoutError, flavor.token_expired):
                await flavor.qr_recreate(qr)
                await flavor.qr_wait(qr)
        except flavor.need_password:
            refresh_running = False
            refresher_task.cancel()
            active_qr_tasks.pop(user_id, None)

            try:
                await photo_msg.delete()
            except Exception:
                pass

            states[user_id] = {
                "step": "password",
                "flavor": flavor,
                "flavor_tag": flavor_tag,
                "client": client,
                "phone": None,
            }

            await _send_rich(
                chat_id,
                "<h2>🔐 Two-Factor Authentication Required</h2>\n"
                "<p>This Telegram account is protected with a <b>2FA Cloud Password</b>.</p>\n"
                "<table border=\"1\">\n"
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
        except asyncio.CancelledError:
            refresh_running = False
            refresher_task.cancel()
            return
        except Exception as e:
            log.exception("QR wait failed for %s", user_id)
            refresh_running = False
            refresher_task.cancel()
            active_qr_tasks.pop(user_id, None)
            try:
                await photo_msg.delete()
            except Exception:
                pass
            await _send_rich(
                chat_id,
                "<h2>❌ QR Login Error</h2>\n"
                f"<blockquote>An error occurred during QR login:<br/><code>{e}</code></blockquote>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📞 Login with Phone", callback_data=f"gen_phone:{flavor_tag}", style=S.PRIMARY)],
                    [InlineKeyboardButton("🔄 Try Again", callback_data="gen_start", style=S.DEFAULT)],
                ]),
            )
            await _drop(user_id)
            return

        refresh_running = False
        refresher_task.cancel()
        active_qr_tasks.pop(user_id, None)

        try:
            await photo_msg.delete()
        except Exception:
            pass

        status = await _send_rich(chat_id, f"<h3>⏳ Finalizing {flavor.name} session authorization...</h3>")
        await _finish_session(chat_id, user_id, status, states.get(user_id, {"flavor": flavor, "client": client}))

    worker_task = asyncio.create_task(_worker())
    active_qr_tasks[user_id] = {
        "task": worker_task,
        "client": client,
    }


# ── Phone OTP Flow ──

async def _start_phone_flow(chat_id: int | str, user_id: int, flavor, flavor_tag: str, old_message: Message | None = None):
    """Switch to manual phone OTP login flow."""
    await _drop(user_id)
    states[user_id] = {"step": "phone", "flavor": flavor, "flavor_tag": flavor_tag}

    phone_html = (
        f"<h2>📱 {flavor.name} Phone Setup</h2>\n"
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
        "<blockquote>Send your phone number as a message (e.g. <code>+1234567890</code>):</blockquote>"
    )

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Scan QR Code instead", callback_data=f"gen_qr:{flavor_tag}", style=S.PRIMARY)],
        [InlineKeyboardButton("❌ Cancel", callback_data="gen_cancel", style=S.DANGER)],
    ])

    if old_message:
        try:
            await old_message.delete()
        except Exception:
            pass

    await _send_rich(chat_id, phone_html, reply_markup=markup)


# ── Flow callbacks & handlers ──

@bot.on_callback_query(filters.regex("^gen_cancel$"))
async def cb_cancel(_, cq: CallbackQuery):
    await _drop(cq.from_user.id)
    await cq.answer("Generation cancelled.", show_alert=False)
    try:
        await cq.message.delete()
    except Exception:
        pass
    await _send_rich(
        cq.message.chat.id,
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
    flavor_tag = "tele" if cq.data == "gen_choose:tele" else "pyro"
    flavor = Tele if flavor_tag == "tele" else Pyro
    await cq.answer(f"Starting {flavor.name} QR Login...", show_alert=False)
    await _start_qr_flow(cq.message.chat.id, user_id, flavor, flavor_tag, old_message=cq.message)


@bot.on_callback_query(filters.regex("^gen_phone:"))
async def cb_phone_switch(_, cq: CallbackQuery):
    user_id = cq.from_user.id
    flavor_tag = "tele" if cq.data == "gen_phone:tele" else "pyro"
    flavor = Tele if flavor_tag == "tele" else Pyro
    await cq.answer(f"Switched to {flavor.name} Phone OTP", show_alert=False)
    await _start_phone_flow(cq.message.chat.id, user_id, flavor, flavor_tag, old_message=cq.message)


@bot.on_callback_query(filters.regex("^gen_qr:"))
async def cb_qr_switch(_, cq: CallbackQuery):
    user_id = cq.from_user.id
    flavor_tag = "tele" if cq.data == "gen_qr:tele" else "pyro"
    flavor = Tele if flavor_tag == "tele" else Pyro
    await cq.answer(f"Switched to {flavor.name} QR Login", show_alert=False)
    await _start_qr_flow(cq.message.chat.id, user_id, flavor, flavor_tag, old_message=cq.message)


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
    user_id = message.from_user.id

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
        state["client"] = client = flavor.client(user_id)
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
            user_id,
            status,
            "<h2>❌ Invalid Phone Number</h2>\n"
            "<blockquote>The phone number entered is not valid or not registered on Telegram.</blockquote>",
        )
    except flavor.flood as e:
        secs = flood_seconds(e)
        await _fail(
            user_id,
            status,
            "<h2>⚠️ Rate Limited</h2>\n"
            f"<blockquote>Too many attempts. Telegram requires you to wait <b>{secs}</b> seconds before trying again.</blockquote>",
        )
    except Exception as e:
        log.exception("phone step failed for %s", user_id)
        await _fail(
            user_id,
            status,
            "<h2>❌ Connection Error</h2>\n"
            f"<blockquote>Failed to request verification code:<br/><code>{e}</code></blockquote>",
        )


async def _handle_code(message: Message, code: str, state: dict):
    flavor = state["flavor"]
    user_id = message.from_user.id
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
            user_id,
            status,
            "<h2>❌ Verification Code Expired</h2>\n"
            "<blockquote>The verification code has expired. Please start again.</blockquote>",
        )
        return
    except Exception as e:
        log.exception("code step failed for %s", user_id)
        await _fail(
            user_id,
            status,
            "<h2>❌ Verification Error</h2>\n"
            f"<blockquote>An error occurred during verification:<br/><code>{e}</code></blockquote>",
        )
        return

    await _finish_session(message.chat.id, user_id, status, state)


async def _handle_password(message: Message, password: str, state: dict):
    flavor = state["flavor"]
    user_id = message.from_user.id

    draft_id = bot.rnd_id()
    await _draft(message.chat.id, draft_id, "<h3>⏳ Verifying 2FA password...</h3>")

    status = await _send_rich(message.chat.id, "<h3>⏳ Verifying 2FA password...</h3>")

    try:
        await flavor.check_password(state["client"], state.get("phone"), password.strip())
    except flavor.password_invalid:
        await _edit(
            status,
            "<h2>❌ Incorrect 2FA Password</h2>\n"
            "<blockquote>The 2FA password entered is incorrect. Please try again:</blockquote>",
            reply_markup=_back_kb(),
        )
        return
    except Exception as e:
        log.exception("password step failed for %s", user_id)
        await _fail(
            user_id,
            status,
            "<h2>❌ Password Verification Error</h2>\n"
            f"<blockquote>An error occurred during password verification:<br/><code>{e}</code></blockquote>",
        )
        return

    await _finish_session(message.chat.id, user_id, status, state)


async def _finish_session(chat_id: int | str, user_id: int, status: Message, state: dict):
    """Finalize authorization, join broadcast channels, and present the generated string session."""
    flavor = state["flavor"]
    client = state["client"]
    draft_id = bot.rnd_id()

    try:
        await _draft(chat_id, draft_id, "<h3>⏳ Finalizing session authorization...</h3>")
        for chat in (GROUP, CHANNEL):
            try:
                await flavor.join(client, chat)
            except Exception:
                pass
        await _draft(chat_id, draft_id, f"<h3>⚡ Exporting {flavor.name} session string...</h3>")
        string_session = await flavor.export(client)
    except Exception as e:
        log.exception("session export failed for %s", user_id)
        await _fail(
            user_id,
            status,
            "<h2>❌ Export Error</h2>\n"
            f"<blockquote>Failed to export string session:<br/><code>{e}</code></blockquote>",
        )
        return

    await _drop(user_id)

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
        f"    <td><code>{user_id}</code></td>\n"
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
    await _drop(message.from_user.id)  # restarting mid-flow must not leak old tasks/clients
    await _send_rich(
        message.chat.id,
        _choice_html(),
        reply_markup=_choice_kb(),
    )


@bot.on_message(filters.command(["qr", "qrlogin"]) & filters.private)
async def start_qr_command(_, message: Message):
    await _drop(message.from_user.id)
    args = message.command[1:] if len(message.command) > 1 else []
    if args and args[0].lower() in ("tele", "telethon"):
        await _start_qr_flow(message.chat.id, message.from_user.id, Tele, "tele")
    elif args and args[0].lower() in ("pyro", "pyrogram"):
        await _start_qr_flow(message.chat.id, message.from_user.id, Pyro, "pyro")
    else:
        await _send_rich(
            message.chat.id,
            "<h2>⚡ Instant QR Code Login</h2>\n"
            "<p>Select your preferred framework to generate a login QR code instantly:</p>\n"
            "<table border=\"1\">\n"
            "  <tr>\n"
            "    <td><b>Framework</b></td>\n"
            "    <td><b>Description</b></td>\n"
            "  </tr>\n"
            "  <tr>\n"
            "    <td><b>Pyrogram</b></td>\n"
            "    <td>Modern async MTProto framework</td>\n"
            "  </tr>\n"
            "  <tr>\n"
            "    <td><b>Telethon</b></td>\n"
            "    <td>Classic asyncio Telegram client</td>\n"
            "  </tr>\n"
            "</table>\n\n"
            "<blockquote>Select a framework below to continue:</blockquote>",
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
        "  <tr>\n"
        "    <td><code>/qr</code></td>\n"
        "    <td>Direct QR Code scan login</td>\n"
        "  </tr>\n"
        "</table>\n\n"
        "<details>\n"
        "  <summary>📖 How to Generate a Session</summary>\n"
        "  <p><b>1.</b> Send <code>/gen</code> or <code>/qr</code> to begin.<br/>\n"
        "  <b>2.</b> Choose your preferred framework (<b>Pyrogram</b> or <b>Telethon</b>).<br/>\n"
        "  <b>3.</b> Scan the instant QR code via <b>Telegram Settings ➔ Devices ➔ Link Desktop Device</b> (or tap Phone Login for OTP).<br/>\n"
        "  <b>4.</b> Enter your Two-Step Verification (2FA) password if enabled.<br/>\n"
        "  <b>5.</b> Instantly receive your session string!</p>\n"
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
    log.info("Starting bot with Kurigram Bot API 10.2 UI & QR Login...")
    bot.run()

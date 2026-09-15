"""Session string generator bot for Pyrogram and Telethon with Kurigram Bot API 10.2 UI & QR Login."""
import asyncio
import io
import logging
import re
import time

import qrcode
from pyrogram import Client, filters
from pyrogram.enums import ButtonStyle as S, ParseMode
from pyrogram.errors import MessageNotModified, FloodWait
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputRichMessage,
    Message,
)

from config import API_ID, API_HASH, BOT_TOKEN, GROUP, CHANNEL, ADMIN_IDS
from database import save_user, get_all_users, get_users_count
from flavors import Pyro, Tele, flood_seconds

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger(__name__)

bot = Client("sessionbot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)

# One active generation flow per user; keyed by user id.
states: dict[int, dict] = {}
# Active QR background worker tasks; keyed by user id.
active_qr_tasks: dict[int, dict] = {}


def is_admin(user_id: int) -> bool:
    """Check if the user is in ADMIN_IDS."""
    return bool(ADMIN_IDS and user_id in ADMIN_IDS)


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
    target: Message | tuple[int | str, int],
    rich_html: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message | None:
    """Edit an existing message using native Rich HTML."""
    if isinstance(target, Message):
        chat_id = target.chat.id
        message_id = target.id
    else:
        chat_id, message_id = target

    try:
        return await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            rich_message=InputRichMessage(html=rich_html),
            reply_markup=reply_markup,
        )
    except MessageNotModified:
        return None
    except Exception as e:
        log.warning("Failed to edit message %s in chat %s: %s", message_id, chat_id, e)
        try:
            return await _send_rich(chat_id, rich_html, reply_markup=reply_markup)
        except Exception:
            return None


async def _delete_msg(chat_id: int | str, message_id: int | None):
    if not message_id:
        return
    try:
        await bot.delete_messages(chat_id=chat_id, message_ids=message_id)
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
        "⏱ <i>Expires in 2 minutes (auto-refreshes every 15s).</i>"
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
        flavor = entry.get("flavor", Pyro)
        if client:
            try:
                await flavor.disconnect(client)
            except Exception:
                pass


async def _drop(user_id: int):
    """End the flow: cancel QR tasks, forget state, and disconnect any client."""
    await _cancel_qr_task(user_id)
    state = states.pop(user_id, None)
    if state and state.get("client"):
        try:
            flavor = state.get("flavor", Pyro)
            await flavor.disconnect(state["client"])
        except Exception:
            pass


async def _fail(user_id: int, chat_id: int | str, status_msg_id: int, rich_html: str, reply_markup: InlineKeyboardMarkup | None = None):
    """Clean up state and display a formatted failure rich message."""
    await _drop(user_id)
    if reply_markup is None:
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Try Again", callback_data="gen_start", style=S.PRIMARY)]
        ])
    await _edit((chat_id, status_msg_id), rich_html, reply_markup=reply_markup)


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
            await flavor.disconnect(client)
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
        deadline = time.time() + 120
        user_logged_in = False

        try:
            while time.time() < deadline:
                remaining = max(1.0, deadline - time.time())
                try:
                    await asyncio.wait_for(flavor.qr_wait(qr), timeout=remaining)
                    user_logged_in = True
                    break
                except (asyncio.TimeoutError, flavor.token_expired):
                    if time.time() >= deadline:
                        break
                    try:
                        await flavor.qr_recreate(qr)
                    except Exception:
                        pass
        except flavor.need_password:
            refresh_running = False
            refresher_task.cancel()
            active_qr_tasks.pop(user_id, None)

            try:
                await photo_msg.delete()
            except Exception:
                pass

            pwd_msg = await _send_rich(
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

            states[user_id] = {
                "step": "password",
                "flavor": flavor,
                "flavor_tag": flavor_tag,
                "client": client,
                "phone": None,
                "status_msg_id": pwd_msg.id,
            }
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
        finally:
            refresh_running = False
            refresher_task.cancel()

        if not user_logged_in:
            # Timed out after 120 seconds
            active_qr_tasks.pop(user_id, None)
            try:
                await photo_msg.delete()
            except Exception:
                pass
            await _send_rich(
                chat_id,
                "<h2>⏳ QR Login Timed Out</h2>\n"
                "<blockquote>The QR code session expired after 2 minutes of inactivity.</blockquote>\n\n"
                "<p>Please click below to start a new session when you are ready to scan.</p>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Try Again", callback_data=f"gen_qr:{flavor_tag}", style=S.PRIMARY)],
                    [InlineKeyboardButton("📞 Login with Phone", callback_data=f"gen_phone:{flavor_tag}", style=S.DEFAULT)],
                    [InlineKeyboardButton("❌ Cancel", callback_data="gen_cancel", style=S.DANGER)],
                ]),
            )
            await _drop(user_id)
            return

        active_qr_tasks.pop(user_id, None)
        try:
            await photo_msg.delete()
        except Exception:
            pass

        status = await _send_rich(chat_id, f"<h3>⏳ Finalizing {flavor.name} session authorization...</h3>")
        state_dict = states.get(user_id, {"flavor": flavor, "client": client})
        state_dict["status_msg_id"] = status.id
        await _finish_session(chat_id, user_id, status.id, state_dict)

    worker_task = asyncio.create_task(_worker())
    active_qr_tasks[user_id] = {
        "task": worker_task,
        "client": client,
        "flavor": flavor,
    }


# ── Phone OTP Flow ──

async def _start_phone_flow(chat_id: int | str, user_id: int, flavor, flavor_tag: str, old_message: Message | None = None):
    """Switch to manual phone OTP login flow."""
    await _drop(user_id)

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

    prompt_msg = await _send_rich(chat_id, phone_html, reply_markup=markup)
    states[user_id] = {
        "step": "phone",
        "flavor": flavor,
        "flavor_tag": flavor_tag,
        "prompt_msg_id": prompt_msg.id,
    }


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


async def _handle_phone(message: Message, phone_input: str, state: dict):
    flavor = state["flavor"]
    user_id = message.from_user.id
    chat_id = message.chat.id

    # Clean previous prompt message if present
    prompt_msg_id = state.get("prompt_msg_id")
    if prompt_msg_id:
        await _delete_msg(chat_id, prompt_msg_id)

    # Normalize phone number (strip whitespace, dashes, parentheses)
    digits = re.sub(r"\D", "", phone_input)
    phone = f"+{digits}"

    if not 6 <= len(digits) <= 16:
        err_msg = await _send_rich(
            chat_id,
            "<h2>❌ Invalid Phone Number</h2>\n"
            "<blockquote>Please send your full phone number with country code (e.g. <code>+1234567890</code>).</blockquote>",
            reply_markup=_back_kb(),
        )
        state["prompt_msg_id"] = err_msg.id
        return

    status = await _send_rich(
        chat_id,
        f"<h3>⏳ Requesting verification code for <code>{phone}</code>...</h3>",
    )
    status_id = status.id
    state["status_msg_id"] = status_id

    try:
        state["client"] = client = flavor.client(user_id)
        await client.connect()
        phone_code_hash = await flavor.send_code(client, phone)
        state.update({
            "step": "code",
            "phone": phone,
            "phone_code_hash": phone_code_hash,
            "status_msg_id": status_id,
        })

        await _edit(
            (chat_id, status_id),
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
            chat_id,
            status_id,
            "<h2>❌ Invalid Phone Number</h2>\n"
            "<blockquote>The phone number entered is not valid or not registered on Telegram.</blockquote>",
        )
    except flavor.flood as e:
        secs = flood_seconds(e)
        await _fail(
            user_id,
            chat_id,
            status_id,
            "<h2>⚠️ Rate Limited</h2>\n"
            f"<blockquote>Too many attempts. Telegram requires you to wait <b>{secs}</b> seconds before trying again.</blockquote>",
        )
    except Exception as e:
        log.exception("phone step failed for %s", user_id)
        await _fail(
            user_id,
            chat_id,
            status_id,
            "<h2>❌ Connection Error</h2>\n"
            f"<blockquote>Failed to request verification code:<br/><code>{e}</code></blockquote>",
        )


async def _handle_code(message: Message, code_input: str, state: dict):
    flavor = state["flavor"]
    user_id = message.from_user.id
    chat_id = message.chat.id
    status_id = state.get("status_msg_id")

    # Delete incoming code message for security
    try:
        await message.delete()
    except Exception:
        pass

    clean_code = re.sub(r"\D", "", code_input)
    if not clean_code:
        clean_code = code_input.strip()

    if status_id:
        await _edit((chat_id, status_id), "<h3>⏳ Verifying authorization code...</h3>")
    else:
        status = await _send_rich(chat_id, "<h3>⏳ Verifying authorization code...</h3>")
        status_id = status.id
        state["status_msg_id"] = status_id

    try:
        await flavor.sign_in(state["client"], state["phone"], state["phone_code_hash"], clean_code)
    except flavor.need_password:
        state["step"] = "password"
        await _edit(
            (chat_id, status_id),
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
            (chat_id, status_id),
            "<h2>❌ Invalid Verification Code</h2>\n"
            "<blockquote>The code you entered is incorrect. Please try again (e.g. <code>1 2 3 4 5</code>):</blockquote>",
            reply_markup=_back_kb(),
        )
        return
    except flavor.code_expired:
        await _fail(
            user_id,
            chat_id,
            status_id,
            "<h2>❌ Verification Code Expired</h2>\n"
            "<blockquote>The verification code has expired. Please start again.</blockquote>",
        )
        return
    except flavor.flood as e:
        secs = flood_seconds(e)
        await _fail(
            user_id,
            chat_id,
            status_id,
            "<h2>⚠️ Rate Limited</h2>\n"
            f"<blockquote>Too many attempts. Telegram requires you to wait <b>{secs}</b> seconds before trying again.</blockquote>",
        )
        return
    except Exception as e:
        log.exception("code step failed for %s", user_id)
        await _fail(
            user_id,
            chat_id,
            status_id,
            "<h2>❌ Verification Error</h2>\n"
            f"<blockquote>An error occurred during verification:<br/><code>{e}</code></blockquote>",
        )
        return

    await _finish_session(chat_id, user_id, status_id, state)


async def _handle_password(message: Message, password: str, state: dict):
    flavor = state["flavor"]
    user_id = message.from_user.id
    chat_id = message.chat.id
    status_id = state.get("status_msg_id")

    # Delete incoming password message for security
    try:
        await message.delete()
    except Exception:
        pass

    if status_id:
        await _edit((chat_id, status_id), "<h3>⏳ Verifying 2FA password...</h3>")
    else:
        status = await _send_rich(chat_id, "<h3>⏳ Verifying 2FA password...</h3>")
        status_id = status.id
        state["status_msg_id"] = status_id

    try:
        await flavor.check_password(state["client"], state.get("phone"), password.strip())
    except flavor.password_invalid:
        await _edit(
            (chat_id, status_id),
            "<h2>❌ Incorrect 2FA Password</h2>\n"
            "<blockquote>The 2FA password entered is incorrect. Please try again:</blockquote>",
            reply_markup=_back_kb(),
        )
        return
    except flavor.flood as e:
        secs = flood_seconds(e)
        await _fail(
            user_id,
            chat_id,
            status_id,
            "<h2>⚠️ Rate Limited</h2>\n"
            f"<blockquote>Too many attempts. Telegram requires you to wait <b>{secs}</b> seconds before trying again.</blockquote>",
        )
        return
    except Exception as e:
        log.exception("password step failed for %s", user_id)
        await _fail(
            user_id,
            chat_id,
            status_id,
            "<h2>❌ Password Verification Error</h2>\n"
            f"<blockquote>An error occurred during password verification:<br/><code>{e}</code></blockquote>",
        )
        return

    await _finish_session(chat_id, user_id, status_id, state)


async def _finish_session(chat_id: int | str, user_id: int, status_msg_id: int, state: dict):
    """Finalize authorization, join broadcast channels, and present the generated string session."""
    flavor = state["flavor"]
    client = state["client"]

    await _edit((chat_id, status_msg_id), f"<h3>⏳ Finalizing {flavor.name} session authorization...</h3>")

    try:
        for chat in (GROUP, CHANNEL):
            if chat:
                try:
                    await flavor.join(client, chat)
                except Exception:
                    pass
        string_session = await flavor.export(client)
    except Exception as e:
        log.exception("session export failed for %s", user_id)
        await _fail(
            user_id,
            chat_id,
            status_msg_id,
            "<h2>❌ Export Error</h2>\n"
            f"<blockquote>Failed to export string session:<br/><code>{e}</code></blockquote>",
        )
        return

    await _drop(user_id)

    me = await bot.get_me()
    await _edit(
        (chat_id, status_msg_id),
        f"<h1>🎉 Session Generated Successfully!</h1>\n"
        f"<p>Here is your <b>{flavor.name}</b> session string (tap to copy):</p>\n"
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
        "<h3>🔑 String Session:</h3>\n"
        f"<code>{string_session}</code>\n\n"
        "<details>\n"
        "  <summary>🛡️ Critical Security Instructions</summary>\n"
        "  <p>• <b>Confidential:</b> Anyone with this string can access your Telegram account.<br/>\n"
        "  • <b>Revocation:</b> You can terminate this active session anytime via <b>Telegram Settings → Devices</b>.<br/>\n"
        "  • <b>Best Practice:</b> Never paste this string into public chats or insecure scripts.</p>\n"
        "</details>\n\n"
        "<p>⚠️ <b>Important:</b> Keep this string session private and secure!</p>",
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
    asyncio.create_task(save_user(user_id))


# ── Static handlers ──

@bot.on_message(filters.command("gen") & filters.private)
async def start_gen(_, message: Message):
    asyncio.create_task(save_user(message.from_user.id))
    await _drop(message.from_user.id)
    await _send_rich(
        message.chat.id,
        _choice_html(),
        reply_markup=_choice_kb(),
    )


@bot.on_message(filters.command(["qr", "qrlogin"]) & filters.private)
async def start_qr_command(_, message: Message):
    asyncio.create_task(save_user(message.from_user.id))
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
    asyncio.create_task(save_user(message.from_user.id))
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


# ── Broadcast & Admin Commands (OTPBOT parity) ──

@bot.on_callback_query(filters.regex(r"^broadcast_help$"))
async def cb_broadcast_help(_, cq: CallbackQuery):
    if not is_admin(cq.from_user.id):
        await cq.answer("⛔ Admin only.", show_alert=True)
        return
    await _edit(
        cq.message,
        "📢 <b>Broadcast Message</b>\n\n"
        "Reply to any message with:\n\n"
        "• <code>/broadcast</code> — copies message to all users (no sender shown)\n"
        "• <code>/broadcast -name</code> — forwards message (original sender visible)\n\n"
        "Or send directly:\n"
        "• <code>/broadcast &lt;message text&gt;</code>\n\n"
        "📌 Must be used as a <b>reply</b> or with direct text.",
    )


@bot.on_message(filters.command(["broadcast", "bcast"]) & filters.private)
async def cmd_broadcast(_, message: Message):
    if not is_admin(message.from_user.id):
        await message.reply("⛔ Admin only command.")
        return

    target = message.reply_to_message
    args = message.text.split(None, 1)
    flag_or_text = args[1].strip() if len(args) > 1 else ""

    if not target and not flag_or_text:
        await message.reply(
            "❌ <b>Reply to a message</b> to broadcast it, or provide text.\n\n"
            "<blockquote>"
            "• <code>/broadcast</code> — copies replied message (no sender shown)\n"
            "• <code>/broadcast -name</code> — forwards replied message (sender visible)\n"
            "• <code>/broadcast &lt;message&gt;</code> — direct text broadcast to all users"
            "</blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return

    include_name = flag_or_text.lower() == "-name"
    if flag_or_text and target and not include_name:
        await message.reply(
            "❌ Unknown flag. Use <code>/broadcast</code> or <code>/broadcast -name</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    direct_text = None
    if not target:
        direct_text = flag_or_text

    users = await get_all_users()
    if not users:
        await message.reply("⚠️ No users found in database to broadcast to.")
        return

    status_msg = await message.reply(
        f"⏳ Broadcasting to {len(users)} users...",
        parse_mode=ParseMode.HTML,
    )

    sent = 0
    failed = 0
    total = len(users)

    for idx, uid in enumerate(users, start=1):
        try:
            if target:
                if include_name:
                    await target.forward(uid)
                else:
                    await target.copy(uid)
            else:
                await bot.send_message(uid, direct_text, parse_mode=ParseMode.HTML)
            sent += 1
        except FloodWait as e:
            await asyncio.sleep(e.value)
            try:
                if target:
                    if include_name:
                        await target.forward(uid)
                    else:
                        await target.copy(uid)
                else:
                    await bot.send_message(uid, direct_text, parse_mode=ParseMode.HTML)
                sent += 1
            except Exception:
                failed += 1
        except Exception:
            failed += 1

        # Periodic status updates every 25 users
        if idx % 25 == 0:
            pct = (idx / total * 100) if total else 0
            await _edit(
                status_msg,
                f"⏳ <b>Broadcasting to {total} users...</b>\n\n"
                f"• Progress: <code>{idx}/{total}</code> ({pct:.1f}%)\n"
                f"• Sent: <b>{sent}</b> • Failed: <b>{failed}</b>",
            )

        await asyncio.sleep(0.05)  # ~20 msg/s, within Telegram limits

    await _edit(
        status_msg,
        f"📢 <b>Broadcast Complete!</b>\n\n"
        f"• <b>Total Targeted:</b> <code>{total}</code>\n"
        f"• <b>Successfully Sent:</b> <code>{sent}</code>\n"
        f"• <b>Failed / Blocked:</b> <code>{failed}</code>",
    )


@bot.on_message(filters.command(["stats", "users"]) & filters.private)
async def cmd_stats(_, message: Message):
    if not is_admin(message.from_user.id):
        return
    count = await get_users_count()
    await _send_rich(
        message.chat.id,
        f"<h2>📊 Session-Gen Statistics</h2>\n"
        f"<table border=\"1\">\n"
        f"  <tr><td><b>Metric</b></td><td><b>Value</b></td></tr>\n"
        f"  <tr><td>👥 Registered Users</td><td><code>{count}</code></td></tr>\n"
        f"</table>\n\n"
        f"<blockquote>Use <code>/broadcast</code> to send announcements to all users.</blockquote>",
    )


if __name__ == "__main__":
    log.info("Starting bot with Kurigram Bot API 10.2 UI & QR Login...")
    bot.run()

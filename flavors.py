"""Per-library adapters: identical auth flow, different SDK calls."""
from pyrogram import Client
from pyrogram.errors import (
    FloodWait,
    PasswordHashInvalid,
    PhoneCodeExpired,
    PhoneCodeInvalid,
    PhoneNumberInvalid,
    SessionPasswordNeeded,
)
from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest

from config import API_ID, API_HASH


def flood_seconds(exc) -> str:
    """Pyrogram exposes FloodWait.value, Telethon exposes FloodWaitError.seconds."""
    secs = getattr(exc, "value", getattr(exc, "seconds", None))
    return "?" if secs is None else str(secs)


class Pyro:
    name = "Pyrogram"
    code_invalid = PhoneCodeInvalid
    code_expired = PhoneCodeExpired
    need_password = SessionPasswordNeeded
    password_invalid = PasswordHashInvalid
    phone_invalid = PhoneNumberInvalid
    flood = FloodWait

    @staticmethod
    def client(sender):
        return Client(f"user_{sender}", api_id=API_ID, api_hash=API_HASH, in_memory=True)

    @staticmethod
    async def send_code(client, phone):
        return (await client.send_code(phone)).phone_code_hash

    @staticmethod
    async def sign_in(client, phone, phone_code_hash, code):
        await client.sign_in(phone, phone_code_hash, code)

    @staticmethod
    async def check_password(client, phone, password):
        await client.check_password(password)

    @staticmethod
    async def export(client):
        return await client.export_session_string()

    @staticmethod
    async def join(client, chat):
        await client.join_chat(chat)


class Tele:
    name = "Telethon"
    code_invalid = errors.PhoneCodeInvalidError
    code_expired = errors.PhoneCodeExpiredError
    need_password = errors.SessionPasswordNeededError
    password_invalid = errors.PasswordHashInvalidError
    phone_invalid = errors.PhoneNumberInvalidError
    flood = errors.FloodWaitError

    @staticmethod
    def client(sender):
        return TelegramClient(StringSession(), API_ID, API_HASH)

    @staticmethod
    async def send_code(client, phone):
        return (await client.send_code_request(phone)).phone_code_hash

    @staticmethod
    async def sign_in(client, phone, phone_code_hash, code):
        await client.sign_in(phone, code, phone_code_hash=phone_code_hash)

    @staticmethod
    async def check_password(client, phone, password):
        await client.sign_in(phone=phone, password=password)

    @staticmethod
    async def export(client):
        return client.session.save()

    @staticmethod
    async def join(client, chat):
        await client(JoinChannelRequest(chat))


if __name__ == "__main__":
    surface = {n for n in vars(Pyro) if not n.startswith("_")}
    assert surface == {n for n in vars(Tele) if not n.startswith("_")}, "flavor surfaces differ"
    errs = ["code_invalid", "code_expired", "need_password", "password_invalid", "phone_invalid", "flood"]
    for flavor in (Pyro, Tele):
        for attr in errs:
            err = getattr(flavor, attr)
            assert isinstance(err, type) and issubclass(err, Exception), (flavor.name, attr, err)
        # distinct classes, else except-clauses shadow each other
        assert len({getattr(flavor, a) for a in errs}) == len(errs), f"{flavor.name} has duplicate error classes"

    assert flood_seconds(FloodWait(30)) == "30"
    assert flood_seconds(FloodWait(0)) == "0"  # 0 must not read as "unknown"
    assert flood_seconds(errors.FloodWaitError(request=None, capture=42)) == "42"
    assert flood_seconds(Exception()) == "?"
    print("ok")

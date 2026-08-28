# Session Generator Bot

A secure Telegram bot that generates **Pyrogram** and **Telethon** session strings for users. This bot helps users create session strings safely and securely for their Telegram accounts.

## Features

- ⚡ **Instant QR Code Login**: Scan directly via **Telegram Settings ➔ Devices ➔ Link Desktop Device** for fast & seamless authorization
- 🔄 **Auto-Refreshing QR Codes**: Dynamically refreshes MTProto login tokens every 15 seconds
- 🎨 **Kurigram Bot API 10.2 UI**: Native Rich Messages, HTML tables, collapsible `<details>`, styled buttons, and streaming drafts
- 🔐 **Secure Session Generation**: Safely generates Pyrogram and Telethon session strings
- 🧩 **Both Libraries**: Pick Pyrogram or Telethon with styled inline buttons
- 📱 **Phone Number Verification Fallback**: Supports international phone number format and OTP verification
- 🔢 **2FA Support**: Handles two-factor authentication with in-memory security
- ⚡ **Fast & Reliable**: Live streaming draft updates during authorization
- 🛡️ **Privacy Focused**: Keeps user data secure and isolated
- 🐳 **Docker Ready**: Easy deployment with Docker

## Prerequisites

- Python 3.8+
- Telegram Bot Token
- Telegram API ID and API Hash

## Installation

### Using Docker (Recommended)

1. Clone the repository:
```bash
git clone https://github.com/nub-coders/Session-gen.git
cd Session-gen
```

2. Build the Docker image:
```bash
docker build -t session .
```

3. Run the container with restart policy:
```bash
docker run -d --name session --restart always session
```

**Note**: The `--restart always` flag ensures the container automatically restarts if it stops unexpectedly.

### Manual Installation

1. Install system dependencies:
```bash
sudo apt-get update
sudo apt-get install ffmpeg libmagic1
```

2. Install Python dependencies:
```bash
pip install -r requirements.txt
```

3. Configure your bot:
   - Copy `.env.example` to `.env` and fill in your values
   - Set `ADMIN_IDS` (comma-separated user IDs) in `.env` if needed

4. Run the bot:
```bash
python main.py
```

## Configuration

Set up your environment variables in `.env`:

```env
API_ID=your_api_id
API_HASH=your_api_hash
BOT_TOKEN=your_bot_token
GROUP=your_support_group
CHANNEL=your_updates_channel
ADMIN_IDS=123456789,987654321
```

### Getting API Credentials

1. Go to [my.telegram.org](https://my.telegram.org)
2. Log in with your phone number
3. Go to "API development tools"
4. Create a new application
5. Copy your API ID and API Hash

### Getting Bot Token

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Create a new bot with `/newbot`
3. Copy the bot token

## Usage

### Bot Commands

- `/start` - Show welcome message and bot information
- `/gen` - Generate a session string (choose Pyrogram or Telethon)
- `/qr` or `/qrlogin` - Direct QR Code scan login

### Generating Session Strings

1. Start a conversation with the bot
2. Send `/gen` or `/qr` command
3. Choose **Pyrogram** or **Telethon**
4. An instant QR code will be generated and auto-refreshed:
   - **QR Scan**: Open Telegram on your phone ➔ **Settings ➔ Devices ➔ Link Desktop Device** and scan the code.
   - **Phone OTP**: Tap **📞 Login with Phone Number** to receive a code via SMS/Telegram instead.
5. If 2FA is enabled, enter your password
6. Receive your session string instantly!

Tap **❌ Cancel** at any step to abort the flow.

## Security Features

- **Non-root User**: Docker container runs as non-root user
- **Session Isolation**: Each user session is isolated
- **Input Validation**: Phone numbers and codes are validated
- **Error Handling**: Comprehensive error handling for various scenarios
- **Admin Controls**: Restricted admin commands

## Project Structure

```
Session-gen/
├── main.py              # Main bot application (kurigram/pyrogram, state-machine & QR worker)
├── flavors.py           # Pyrogram/Telethon adapters + error classes + QRLogin
├── config.py            # Configuration file
├── requirements.txt     # Python dependencies
├── .env.example         # Example environment variables template
├── Dockerfile          # Docker configuration
└── README.md           # This file
```

## Dependencies

- **Pyrogram (kurigram)**: Bot client and Pyrogram session generation
- **Telethon**: Telethon session generation
- **qrcode & pillow**: QR code rendering for MTProto login
- **And more**: See `requirements.txt` for complete list

## Docker Configuration

The Dockerfile includes:
- Latest Python image
- System dependencies (ffmpeg, libmagic)
- Security hardening (non-root user)
- Optimized layer caching
- Environment variables

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## Support

- **Support Group**: [@nub_coder_s](https://t.me/nub_coder_s)
- **Updates Channel**: [@nub_coders](https://t.me/nub_coders)

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Disclaimer

⚠️ **Important**: Never share your session strings with anyone! They provide full access to your Telegram account. This bot is for educational and legitimate use only.

## Changelog

- **v2.2.0**: Added **Instant QR Code Login** support for both Pyrogram and Telethon with auto-refreshing QR media (every 15s), seamless 2FA fallback, `/qr` command, and "Login with Phone Number" toggle button like in Userbot Deployer.
- **v2.1.0**: Full migration to **Kurigram Bot API 10.2 UI** — Native Rich Messages (`send_rich_message`), interactive HTML `<table>` layouts, collapsible `<details>` walkthroughs, `ButtonStyle` inline buttons, streaming drafts (`send_rich_message_draft`) for real-time progress, and ephemeral feedback toasts.
- **v2.0.0**: Added Telethon session generation via /gen → inline button choice; state-machine flow (conversation-lite) replaces telethon's Conversation API; cancel button, code-expiry handling, client cleanup on every exit path; kurigram/pyrogram bot
- **v1.0.0**: Initial release with Pyrogram session generation

---

Made with ❤️ by [@nub_coder_s](https://t.me/nub_coder_s)

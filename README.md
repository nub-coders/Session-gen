# Session Generator Bot

A secure Telegram bot that generates Pyrogram session strings for users. This bot helps users create session strings safely and securely for their Telegram accounts.

## Features

- 🔐 **Secure Session Generation**: Safely generates Pyrogram session strings
- 📱 **Phone Number Verification**: Supports international phone number format
- 🔢 **2FA Support**: Handles two-factor authentication
- ⚡ **Fast & Reliable**: Quick session string generation
- 🛡️ **Privacy Focused**: Keeps user data secure
- 🐳 **Docker Ready**: Easy deployment with Docker

## Prerequisites

- Python 3.8+
- Telegram Bot Token
- Telegram API ID and API Hash
- MongoDB connection (optional, for user tracking)

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
   - Update `config.py` with your API credentials
   - Add admin user IDs to `admin.txt` (one per line)

4. Run the bot:
```bash
python main.py
```

## Configuration

Edit `config.py` to set up your bot:

```python
API_ID = 'your_api_id'
API_HASH = 'your_api_hash'
BOT_TOKEN = 'your_bot_token'
GROUP = "your_support_group"
CHANNEL = "your_updates_channel"
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
- `/gen` - Generate a new Pyrogram session string
- `/reboot` - Restart the bot (admin only)

### Generating Session Strings

1. Start a conversation with the bot
2. Send `/gen` command
3. Enter your phone number in international format (e.g., +1234567890)
4. Enter the verification code sent to your Telegram
5. If 2FA is enabled, enter your password
6. Receive your session string

## Security Features

- **Non-root User**: Docker container runs as non-root user
- **Session Isolation**: Each user session is isolated
- **Input Validation**: Phone numbers and codes are validated
- **Error Handling**: Comprehensive error handling for various scenarios
- **Admin Controls**: Restricted admin commands

## Project Structure

```
Session-gen/
├── main.py              # Main bot application
├── config.py            # Configuration file
├── requirements.txt     # Python dependencies
├── admin.txt           # Admin user IDs
├── Dockerfile          # Docker configuration
├── session.sh          # Shell script (if any)
└── README.md           # This file
```

## Dependencies

- **Pyrogram**: Telegram client library
- **Telethon**: Alternative Telegram client
- **Pillow**: Image processing
- **PyMongo**: MongoDB driver
- **FFmpeg**: Media processing
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

- **v1.0.0**: Initial release with basic session generation
- Added Docker support
- Enhanced security features
- Improved error handling

---

Made with ❤️ by [@nub_coder_s](https://t.me/nub_coder_s)

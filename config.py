import os

from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
GROUP = os.environ.get("GROUP", "nub_coder_s")
CHANNEL = os.environ.get("CHANNEL", "nub_coders")
ADMIN_IDS = [
    int(x) for x in os.environ.get("ADMIN_IDS", "").replace(",", " ").split() if x.isdigit()
]

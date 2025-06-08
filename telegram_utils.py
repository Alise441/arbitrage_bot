import requests
import logging
from config import config

logger = logging.getLogger(__name__)

# ---- Telegram Notification ----
def send_telegram_message(message: str):
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.warning("Telegram token or chat ID not set. Skipping Telegram notification.")
        return
    url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": config.TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=payload, timeout=5)
    except Exception as e:
        logger.warning(f"Failed to send Telegram message: {e}")
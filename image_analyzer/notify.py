import sys
import time

import requests


def send_telegram_alert(config, img_path, caption, chat_id):
    """Sends the analyzed photo directly to Telegram with a summary caption."""
    if not config.telegram_token or not chat_id:
        print("Telegram configuration or Chat ID missing. Skipping notification.", file=sys.stderr)
        return False

    url = f"https://api.telegram.org/bot{config.telegram_token}/sendPhoto"
    payload = {
        "chat_id": chat_id,
        "caption": caption,
        "parse_mode": "Markdown"
    }

    retries = getattr(config, "telegram_retries", 3)
    for attempt in range(retries + 1):
        try:
            with open(img_path, "rb") as photo:
                files = {"photo": photo}
                response = requests.post(url, data=payload, files=files, timeout=15)
                if response.status_code == 200:
                    print("Telegram alert sent successfully.", file=sys.stderr)
                    return True
                else:
                    print(f"Telegram API Error: {response.text} (Attempt {attempt + 1}/{retries + 1})", file=sys.stderr)
        except Exception as e:
            print(f"Failed to send Telegram notification: {e} (Attempt {attempt + 1}/{retries + 1})", file=sys.stderr)
        if attempt < retries:
            time.sleep(2)
    return False

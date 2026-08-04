import sys
import time

import requests


def _send_telegram_media(config, method, file_field, file_path, caption, chat_id):
    """Shared retry/error-handling body for sendPhoto/sendVideo."""
    if not config.telegram_token or not chat_id:
        print("Telegram configuration or Chat ID missing. Skipping notification.", file=sys.stderr)
        return False

    url = f"https://api.telegram.org/bot{config.telegram_token}/{method}"
    payload = {
        "chat_id": chat_id,
        "caption": caption,
        "parse_mode": "Markdown"
    }

    retries = getattr(config, "telegram_retries", 3)
    for attempt in range(retries + 1):
        try:
            with open(file_path, "rb") as media_file:
                files = {file_field: media_file}
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


def send_telegram_alert(config, img_path, caption, chat_id):
    """Sends the analyzed photo directly to Telegram with a summary caption."""
    return _send_telegram_media(config, "sendPhoto", "photo", img_path, caption, chat_id)


def send_telegram_video(config, video_path, caption, chat_id):
    """Sends a video clip (e.g. an mp4 motion-alert attachment) directly to Telegram."""
    return _send_telegram_media(config, "sendVideo", "video", video_path, caption, chat_id)


def _send_webhook_media(webhook_url, file_field, file_path, caption, extra_data=None):
    """POSTs a media file plus metadata as multipart/form-data to an arbitrary webhook URL,
    used as a drop-in alternative to a Telegram alert (e.g. a Home Assistant `webhook`
    automation trigger, which accepts any multipart/form-data or JSON POST)."""
    if not webhook_url:
        return False

    data = {"caption": caption}
    if extra_data:
        data.update(extra_data)

    try:
        with open(file_path, "rb") as media_file:
            files = {file_field: media_file}
            response = requests.post(webhook_url, data=data, files=files, timeout=15)
            if response.status_code < 300:
                print("Webhook alert sent successfully.", file=sys.stderr)
                return True
            print(f"Webhook error: {response.status_code} {response.text}", file=sys.stderr)
    except Exception as e:
        print(f"Failed to send webhook notification: {e}", file=sys.stderr)
    return False


def send_webhook_alert(webhook_url, img_path, caption, extra_data=None):
    """Sends the analyzed photo to a webhook URL (Home Assistant-compatible) instead of Telegram."""
    return _send_webhook_media(webhook_url, "image", img_path, caption, extra_data)


def send_webhook_video(webhook_url, video_path, caption, extra_data=None):
    """Sends a video clip to a webhook URL (Home Assistant-compatible) instead of Telegram."""
    return _send_webhook_media(webhook_url, "video", video_path, caption, extra_data)

import os
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


def _send_webhook_notification(webhook_url, caption, extra_data=None):
    """POSTs metadata (caption + extra_data, e.g. camera_id/filename) as form data to an
    arbitrary webhook URL, used as a drop-in alternative to a Telegram alert (e.g. a Home
    Assistant `webhook` automation trigger). The image/video itself is not attached -- the
    receiver is expected to fetch it back from this service's `/api/image` endpoint using
    the filename in extra_data, so the file isn't uploaded twice."""
    if not webhook_url:
        return False

    data = {"caption": caption}
    if extra_data:
        data.update(extra_data)

    try:
        response = requests.post(webhook_url, data=data, timeout=15)
        if response.status_code < 300:
            print("Webhook alert sent successfully.", file=sys.stderr)
            return True
        print(f"Webhook error: {response.status_code} {response.text}", file=sys.stderr)
    except Exception as e:
        print(f"Failed to send webhook notification: {e}", file=sys.stderr)
    return False


def send_webhook_alert(webhook_url, img_path, caption, extra_data=None):
    """Notifies a webhook URL (Home Assistant-compatible) about an analyzed photo, instead
    of Telegram. Sends `image_filename` so the receiver can fetch the photo itself via
    `/api/image` -- see _send_webhook_notification."""
    data = {"image_filename": os.path.basename(img_path)}
    if extra_data:
        data.update(extra_data)
    return _send_webhook_notification(webhook_url, caption, data)


def send_webhook_video(webhook_url, video_path, caption, extra_data=None):
    """Notifies a webhook URL (Home Assistant-compatible) about an analyzed video clip,
    instead of Telegram. Sends `video_filename` so the receiver can fetch the clip itself
    via `/api/image` -- see _send_webhook_notification."""
    data = {"is_video": "1", "video_filename": os.path.basename(video_path)}
    if extra_data:
        data.update(extra_data)
    return _send_webhook_notification(webhook_url, caption, data)

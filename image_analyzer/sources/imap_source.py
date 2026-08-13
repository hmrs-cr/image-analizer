import os
import re
import sys
import time
from email import message_from_bytes
from email.header import decode_header

from imapclient import IMAPClient

from ..utils import get_camera_folder, is_video_file
from .base import ImageSource


def decode_mime_string(encoded_string):
    """Safely decodes MIME-encoded email headers."""
    if not encoded_string: return ""
    decoded_parts = decode_header(encoded_string)
    result = ""
    for content, charset in decoded_parts:
        if isinstance(content, bytes):
            charset = charset or 'utf-8'
            try: result += content.decode(charset, errors='replace')
            except Exception: result += content.decode('utf-8', errors='replace')
        else: result += str(content)
    return result


DEFAULT_CHANNEL_NAME = "Camera"


def parse_email_body(body_text):
    """Extracts device name and clean channel name from email plain text layout."""
    device_name = "DVR"
    channel_name = DEFAULT_CHANNEL_NAME

    for line in body_text.splitlines():
        if "Nombre del dispositivo:" in line:
            device_name = line.split(":", 1)[1].strip()
        elif "Nombre del canal:" in line:
            raw_channel = line.split(":", 1)[1].strip()
            clean_channel = re.sub(r'^\[.*?\]\s*:\s*', '', raw_channel)
            channel_name = clean_channel.strip()

    return device_name, channel_name


def parse_channel_from_filename(filename):
    """Falls back to the CAMxx segment of the attachment filename when the body has no channel field."""
    match = re.search(r'_(CAM\d+)_\d{8}_\d{6}', filename)
    return match.group(1) if match else None


class ImapEmailSource(ImageSource):
    """Watches a Gmail (or other IMAP) inbox for DVR motion-alert emails and hands off
    each JPG/MP4 attachment to the shared on_image() callback, which routes videos straight
    to a notification and skips analysis for them (see app.py's on_image).
    """

    @classmethod
    def is_configured(cls, config) -> bool:
        return bool(config.email and config.password)

    def _process_message(self, client, msg_id):
        """Fetches message components, decodes metadata text, downloads JPGs, and hands
        each one to the shared pipeline callback."""
        config = self.config
        raw_data = client.fetch([msg_id], ["RFC822"])
        if not raw_data or msg_id not in raw_data: return

        msg = message_from_bytes(raw_data[msg_id][b"RFC822"])

        if config.from_address:
            msg_from = decode_mime_string(msg.get("From", ""))
            if config.from_address not in msg_from: return
        if config.subject:
            msg_subject = decode_mime_string(msg.get("Subject", ""))
            if config.subject not in msg_subject: return

        print(f"\nMatch verified! Processing Email ID: {msg_id}")

        device_name, channel_name = "DVR", DEFAULT_CHANNEL_NAME
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    body_text = part.get_payload(decode=True).decode('utf-8', errors='replace')
                    device_name, channel_name = parse_email_body(body_text)
                except Exception as e:
                    print(f"Error reading text metadata body: {e}", file=sys.stderr)

        has_valid_attachment = False

        for part in msg.walk():
            if part.get_content_maintype() == "multipart": continue
            filename = part.get_filename()
            if filename:
                decoded = decode_header(filename)[0]
                filename = decoded[0].decode(decoded[1]) if decoded[1] else decoded[0]
                if isinstance(filename, bytes): filename = filename.decode('utf-8')

            if filename and (filename.lower().endswith((".jpg", ".jpeg")) or is_video_file(filename)):
                unique_filename = f"{msg_id}_{filename}"
                filepath = os.path.join(config.download_folder, unique_filename)

                if not os.path.exists(filepath):
                    has_valid_attachment = True
                    effective_channel = channel_name
                    if effective_channel == DEFAULT_CHANNEL_NAME:
                        effective_channel = parse_channel_from_filename(filename) or effective_channel
                    camera_folder = get_camera_folder(config, f"{device_name} - {effective_channel}")
                    os.makedirs(camera_folder, exist_ok=True)
                    filepath = os.path.join(camera_folder, unique_filename)
                    with open(filepath, "wb") as f:
                        f.write(part.get_payload(decode=True))
                    print(f"File saved: {filepath}")

                    self.on_image(filepath, device_name, effective_channel, config.telegram_chat_id)

        if has_valid_attachment:
            client.delete_messages([msg_id])
            client.expunge()

    def run(self):
        """Runs the persistent IMAP monitoring loop."""
        config = self.config
        while True:
            try:
                folder_name = getattr(config, "imap_folder", "INBOX") or "INBOX"
                print(f"\nConnecting to {config.imap_server} ()...")
                with IMAPClient(config.imap_server, use_uid=True) as client:
                    client.login(config.email, config.password)
                    client.select_folder(folder_name)
                    print(f"Connection live. Monitoring folder '{folder_name}' via IMAP IDLE...")

                    while True:
                        client.idle()
                        events = client.idle_check(timeout=600)
                        client.idle_done()

                        if events:
                            search_criteria = ["UNSEEN"]
                            if config.from_address:
                                search_criteria += ["FROM", config.from_address]
                            if config.subject:
                                search_criteria += ["SUBJECT", config.subject]
                            messages = client.search(search_criteria)
                            for msg_id in messages:
                                try:
                                    self._process_message(client, msg_id)
                                except Exception as e:
                                    print(f"Error handling message {msg_id}: {e}", file=sys.stderr)
            except (ConnectionError, Exception) as e:
                print(f"Connection dropped or network error: {e}. Re-establishing link in 15 seconds...", file=sys.stderr)
                time.sleep(15)

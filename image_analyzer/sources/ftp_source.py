import os
import queue
import sys
import threading
import time

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

from ..utils import get_camera_folder
from .base import ImageSource


def parse_ftp_users(raw):
    """Parses the '--ftp-users'/FTP_USERS 'user:pass;user2:pass2' format into {username: password}."""
    users = {}
    for entry in (raw or "").split(";"):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        username, password = entry.split(":", 1)
        username = username.strip()
        if username:
            users[username] = password.strip()
    return users


def parse_passive_ports(raw):
    """Parses the '--ftp-passive-ports'/FTP_PASSIVE_PORTS 'start-end' format into a range, or None."""
    raw = (raw or "").strip()
    if not raw or "-" not in raw:
        return None
    start, end = raw.split("-", 1)
    try:
        start, end = int(start.strip()), int(end.strip())
    except ValueError:
        return None
    if start > end:
        return None
    return range(start, end + 1)


def parse_device_channel(username):
    """Splits a 'device-channel' formatted FTP username into (device_name, channel_name)."""
    if "-" in username:
        device_name, channel_name = username.split("-", 1)
        return device_name.strip() or "DVR", channel_name.strip() or "Camera"
    return username, "Camera"


class FtpImageSource(ImageSource):
    """Runs a plain-FTP server that DVR cameras can upload snapshots to directly.

    Each configured FTP user (see --ftp-users) is jailed to its own home directory under
    download_folder, named the same way IMAP-sourced cameras are (via get_camera_folder) so
    /api/image and the dashboard resolve it identically regardless of which source produced it.
    Every uploaded JPG is handed to the shared analysis pipeline via on_image().
    """

    @classmethod
    def is_configured(cls, config) -> bool:
        return bool(parse_ftp_users(getattr(config, "ftp_users", "")))

    def run(self):
        config = self.config
        users = parse_ftp_users(config.ftp_users)

        authorizer = DummyAuthorizer()
        user_cameras = {}
        for username, password in users.items():
            device_name, channel_name = parse_device_channel(username)
            homedir = get_camera_folder(config, f"{device_name} - {channel_name}")
            os.makedirs(homedir, exist_ok=True)
            authorizer.add_user(username, password, homedir, perm="elrw")
            user_cameras[username] = (device_name, channel_name)

        on_image = self.on_image
        chat_id = config.telegram_chat_id

        # Analysis (YOLO/DeepFace/Gemini/Telegram) runs on a dedicated worker thread so it never
        # blocks the FTP server's single-threaded async I/O loop -- otherwise every upload would
        # stall behind the previous one's analysis time.
        analysis_queue = queue.Queue()

        def analysis_worker():
            while True:
                unique_path, device_name, channel_name = analysis_queue.get()
                try:
                    on_image(unique_path, device_name, channel_name, chat_id)
                except Exception as e:
                    print(f"FTP image analysis failed for {unique_path}: {e}", file=sys.stderr)
                finally:
                    analysis_queue.task_done()

        threading.Thread(target=analysis_worker, daemon=True, name="FtpImageSource-analysis").start()

        class ImageUploadHandler(FTPHandler):
            def on_file_received(self, file):
                if not file.lower().endswith((".jpg", ".jpeg")):
                    return
                device_name, channel_name = user_cameras.get(self.username, ("DVR", "Camera"))
                directory, filename = os.path.split(file)
                unique_path = os.path.join(directory, f"{int(time.time() * 1000)}_{filename}")
                os.rename(file, unique_path)
                print(f"FTP upload received: {unique_path}")
                analysis_queue.put((unique_path, device_name, channel_name))

        ImageUploadHandler.authorizer = authorizer

        passive_ports = parse_passive_ports(getattr(config, "ftp_passive_ports", ""))
        if passive_ports:
            ImageUploadHandler.passive_ports = passive_ports

        masquerade_address = getattr(config, "ftp_masquerade_address", "")
        if masquerade_address:
            ImageUploadHandler.masquerade_address = masquerade_address

        host = config.ftp_host
        port = config.ftp_port
        print(f"Starting FTP server on {host}:{port} for users: {', '.join(users)}")
        server = FTPServer((host, port), ImageUploadHandler)
        server.serve_forever()

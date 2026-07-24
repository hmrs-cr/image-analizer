import os
import sys
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

        class ImageUploadHandler(FTPHandler):
            def on_file_received(self, file):
                if not file.lower().endswith((".jpg", ".jpeg")):
                    return
                device_name, channel_name = user_cameras.get(self.username, ("DVR", "Camera"))
                directory, filename = os.path.split(file)
                unique_path = os.path.join(directory, f"{int(time.time() * 1000)}_{filename}")
                os.rename(file, unique_path)
                print(f"FTP upload received: {unique_path}")
                on_image(unique_path, device_name, channel_name, chat_id)

        ImageUploadHandler.authorizer = authorizer

        host = config.ftp_host
        port = config.ftp_port
        print(f"Starting FTP server on {host}:{port} for users: {', '.join(users)}")
        server = FTPServer((host, port), ImageUploadHandler)
        server.serve_forever()

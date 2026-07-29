import os
import sys

import uvicorn
from ultralytics import YOLO

from .config import get_config
from .pipeline import analyze_image, handle_video
from .server import create_app
from .sources import AVAILABLE_SOURCES
from .stats import load_history_from_disk, load_notification_settings, prune_old_images
from .utils import is_video_file


def start_sources(config, on_image):
    """Starts every configured background image source (IMAP, and any future FTP/Dropbox/...
    source added to sources.AVAILABLE_SOURCES) in its own daemon thread."""
    threads = []
    for source_cls in AVAILABLE_SOURCES:
        if source_cls.is_configured(config):
            threads.append(source_cls(config, on_image).start())
        else:
            print(f"{source_cls.__name__} not configured; skipping.", file=sys.stderr)
    return threads


def main():
    config = get_config()

    if not os.path.exists(config.download_folder):
        os.makedirs(config.download_folder)

    prune_old_images(config)
    load_history_from_disk(config)
    load_notification_settings(config)

    print(f"Loading YOLO model ({config.model_name})...", file=sys.stderr)
    model = YOLO(config.model_name)

    def on_image(filepath, device_name, channel_name, chat_id):
        if is_video_file(filepath):
            handle_video(config, model, filepath, device_name=device_name, channel_name=channel_name, chat_id=chat_id)
        else:
            analyze_image(config, model, filepath, device_name=device_name, channel_name=channel_name, chat_id=chat_id)

    start_sources(config, on_image)

    if not config.auth_username or not config.auth_password:
        print("WARNING: AUTH_USERNAME/AUTH_PASSWORD not set. The dashboard and API are unauthenticated!", file=sys.stderr)
    if not config.analyze_shared_secret:
        print("WARNING: ANALYZE_SHARED_SECRET not set. /analyze-image is unauthenticated!", file=sys.stderr)

    app = create_app(config, model)

    print(f"Starting HTTP API server on {config.host}:{config.port}...", file=sys.stderr)
    uvicorn.run(app, host=config.host, port=config.port, log_level="warning")

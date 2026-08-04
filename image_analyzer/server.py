import hmac
import os
import time
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile, Form, Request
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from .pipeline import analyze_image, handle_video
from .stats import (
    FOREVER_SNOOZE_UNTIL,
    MAX_CACHE_SIZE,
    START_TIME,
    get_history_page,
    save_notification_settings,
    set_notify_chat_id,
    set_notify_webhook,
    set_snooze,
    set_target_classes,
    snooze_status,
    stats,
    stats_lock,
)
from .utils import get_camera_folder, is_video_file, mask_settings

# image_analyzer/server.py -> image_analyzer/ -> repo root, where the static dashboard
# files (index.html, style.css, app.js) live.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

_basic_auth = HTTPBasic(auto_error=False)


def require_basic_auth(request: Request, credentials: HTTPBasicCredentials = Depends(_basic_auth)):
    """Validates the Authorization header against the configured basic-auth credentials.

    If no credentials are configured, auth is not enforced (unconfigured is treated as opt-out).
    """
    config = request.app.state.config
    username, password = config.auth_username, config.auth_password
    if not username or not password:
        return
    if not credentials or not (
        hmac.compare_digest(credentials.username, username)
        and hmac.compare_digest(credentials.password, password)
    ):
        raise HTTPException(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Image Analyzer"'},
        )


def require_shared_secret(request: Request, x_analyze_secret: str = Header(default="")):
    """Validates the X-Analyze-Secret header for the automated /analyze-image endpoint.

    If no secret is configured, auth is not enforced (unconfigured is treated as opt-out).
    """
    secret = request.app.state.config.analyze_shared_secret
    if secret and not hmac.compare_digest(x_analyze_secret, secret):
        raise HTTPException(status_code=401)


class SnoozeBody(BaseModel):
    minutes: float | str = 0
    camera: str = "all"
    device: str = ""
    media_type: str = "all"


class NotifyChatBody(BaseModel):
    camera: str = "all"
    device: str = ""
    media_type: str = "all"
    chat_id: str = ""


class NotifyWebhookBody(BaseModel):
    camera: str = "all"
    device: str = ""
    media_type: str = "all"
    webhook: str = ""


class TargetClassesBody(BaseModel):
    camera: str = "all"
    device: str = ""
    classes: str = ""


def create_app(config, model) -> FastAPI:
    app = FastAPI(title="Image Analyzer")
    app.state.config = config
    app.state.model = model

    auth = [Depends(require_basic_auth)]

    @app.get("/", dependencies=auth)
    def index():
        return FileResponse(PROJECT_ROOT / "index.html")

    @app.get("/style.css", dependencies=auth)
    def style():
        return FileResponse(PROJECT_ROOT / "style.css", media_type="text/css")

    @app.get("/app.js", dependencies=auth)
    def app_js():
        return FileResponse(PROJECT_ROOT / "app.js", media_type="application/javascript")

    @app.get("/api/status", dependencies=auth)
    def api_status():
        with stats_lock:
            cameras_data = {
                name: {
                    "pictures_analyzed": cam["pictures_analyzed"],
                    "matches_found": cam["matches_found"],
                    "notifications_sent": cam["notifications_sent"],
                    "snooze": snooze_status(cam["snooze"]),
                    "chat_id": dict(cam["chat_id"]),
                    "webhook": dict(cam["webhook"]),
                    "classes": cam["classes"],
                }
                for name, cam in stats["cameras"].items()
            }
            devices_data = {
                name: {
                    "snooze": snooze_status(dev["snooze"]),
                    "chat_id": dict(dev["chat_id"]),
                    "webhook": dict(dev["webhook"]),
                    "classes": dev["classes"],
                }
                for name, dev in stats["devices"].items()
            }
            return {
                "uptime": time.time() - START_TIME,
                "global": {
                    "pictures_analyzed": stats["global"]["pictures_analyzed"],
                    "matches_found": stats["global"]["matches_found"],
                    "notifications_sent": stats["global"]["notifications_sent"],
                    "snooze": snooze_status(stats["global"]["snooze"]),
                    "chat_id": dict(stats["global"]["chat_id"]),
                    "webhook": dict(stats["global"]["webhook"]),
                    "classes": stats["global"]["classes"],
                },
                "cameras": cameras_data,
                "devices": devices_data,
                "last_detection": stats["last_detection"],
            }

    @app.get("/api/settings", dependencies=auth)
    def api_settings():
        return mask_settings(config)

    @app.get("/api/classes", dependencies=auth)
    def api_classes():
        available = sorted(model.names.values()) if model else []
        return {"available": available, "default": config.classes}

    @app.get("/api/last-image", dependencies=auth)
    def api_last_image():
        with stats_lock:
            image_path = stats["last_image_path"]
        if not image_path or not os.path.exists(image_path):
            raise HTTPException(404, "No image found")
        return FileResponse(image_path, media_type="image/jpeg")

    @app.get("/api/image", dependencies=auth)
    def api_image(camera: str, file: str):
        safe_filename = os.path.basename(unquote(file))
        camera_folder = get_camera_folder(config, unquote(camera))
        image_path = os.path.join(camera_folder, safe_filename)
        if not os.path.exists(image_path):
            raise HTTPException(404, "Image not found")
        content_type = "video/mp4" if is_video_file(image_path) else "image/jpeg"
        return FileResponse(image_path, media_type=content_type)

    @app.get("/api/history", dependencies=auth)
    def api_history(camera: Optional[str] = None, offset: int = 0, limit: int = MAX_CACHE_SIZE):
        offset = max(0, offset)
        limit = max(1, min(limit, 500))  # guard against pathological disk scans
        return get_history_page(config, camera, offset, limit)

    @app.post("/analyze-image", dependencies=[Depends(require_shared_secret)])
    async def analyze_image_endpoint(
        image: UploadFile,
        device_name: str = Form(""),
        channel_name: str = Form(""),
        notify_chat: Optional[str] = Form(None),
        silent: bool = Form(False),
    ):
        image_data = await image.read()
        if not image_data:
            raise HTTPException(400, "Missing image file field named 'image'")

        if not os.path.exists(config.download_folder):
            os.makedirs(config.download_folder)

        timestamp = int(time.time())
        camera_id = f"{device_name or 'DVR'} - {channel_name or 'Camera'}"
        camera_folder = get_camera_folder(config, camera_id)
        os.makedirs(camera_folder, exist_ok=True)
        filename = image.filename or "uploaded_image.jpg"
        filepath = os.path.join(camera_folder, f"upload_{timestamp}_{filename}")

        with open(filepath, "wb") as f:
            f.write(image_data)

        analyze_fn = handle_video if is_video_file(filename) else analyze_image
        return analyze_fn(
            config, model, filepath,
            device_name=device_name or "DVR", channel_name=channel_name or "Camera",
            chat_id=notify_chat, force_chat_id=bool(notify_chat), silent=silent,
        )

    @app.post("/api/snooze", dependencies=auth)
    def api_snooze(body: SnoozeBody):
        if body.media_type not in ("picture", "video", "all"):
            raise HTTPException(400, "media_type must be 'picture', 'video', or 'all'")
        if body.minutes == "forever":
            until_time = FOREVER_SNOOZE_UNTIL
        else:
            minutes = float(body.minutes)
            until_time = time.time() + (minutes * 60) if minutes > 0 else 0.0
        set_snooze(body.camera, body.media_type, until_time, device=body.device)
        save_notification_settings(config)
        return {
            "status": "success", "camera": body.camera, "device": body.device,
            "media_type": body.media_type, "snooze_until": until_time,
        }

    @app.post("/api/notify-chat", dependencies=auth)
    def api_notify_chat(body: NotifyChatBody):
        if body.media_type not in ("picture", "video", "all"):
            raise HTTPException(400, "media_type must be 'picture', 'video', or 'all'")
        chat_id_value = body.chat_id.strip()
        set_notify_chat_id(body.camera, body.media_type, chat_id_value, device=body.device)
        save_notification_settings(config)
        return {
            "status": "success", "camera": body.camera, "device": body.device,
            "media_type": body.media_type, "chat_id": chat_id_value,
        }

    @app.post("/api/notify-webhook", dependencies=auth)
    def api_notify_webhook(body: NotifyWebhookBody):
        if body.media_type not in ("picture", "video", "all"):
            raise HTTPException(400, "media_type must be 'picture', 'video', or 'all'")
        webhook_value = body.webhook.strip()
        set_notify_webhook(body.camera, body.media_type, webhook_value, device=body.device)
        save_notification_settings(config)
        return {
            "status": "success", "camera": body.camera, "device": body.device,
            "media_type": body.media_type, "webhook": webhook_value,
        }

    @app.post("/api/target-classes", dependencies=auth)
    def api_target_classes(body: TargetClassesBody):
        classes_value = body.classes.strip()
        set_target_classes(body.camera, classes_value, device=body.device)
        save_notification_settings(config)
        return {"status": "success", "camera": body.camera, "device": body.device, "classes": classes_value}

    @app.post("/api/trigger-analysis", dependencies=auth)
    def api_trigger_analysis():
        with stats_lock:
            last_image_path = stats["last_image_path"]
            last_dev, last_chan = stats.get("last_image_camera") or ("DVR", "Camera")
        if not last_image_path or not os.path.exists(last_image_path):
            raise HTTPException(400, "No last image path available to re-analyze")
        return analyze_image(
            config, model, last_image_path,
            device_name=last_dev, channel_name=last_chan, chat_id=config.telegram_chat_id,
        )

    return app

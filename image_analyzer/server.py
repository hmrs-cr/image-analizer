import base64
import hmac
import json
import os
import time
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .pipeline import analyze_image, handle_video
from .stats import (
    FOREVER_SNOOZE_UNTIL,
    MAX_CACHE_SIZE,
    START_TIME,
    get_history_page,
    save_notification_settings,
    set_notify_chat_id,
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


class UploadHandler(BaseHTTPRequestHandler):
    config = None
    model = None

    def check_basic_auth(self):
        """Validates the Authorization header against the configured basic-auth credentials.

        If no credentials are configured, auth is not enforced (unconfigured is treated as opt-out).
        """
        username = self.config.auth_username
        password = self.config.auth_password
        if not username or not password:
            return True

        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False

        try:
            decoded = base64.b64decode(header[len("Basic "):]).decode("utf-8")
            supplied_user, supplied_pass = decoded.split(":", 1)
        except Exception:
            return False

        return hmac.compare_digest(supplied_user, username) and hmac.compare_digest(supplied_pass, password)

    def require_basic_auth(self):
        if self.check_basic_auth():
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Image Analyzer"')
        self.end_headers()
        self.wfile.write(b"Unauthorized")
        return False

    def check_shared_secret(self):
        """Validates the X-Analyze-Secret header for the automated /analyze-image endpoint.

        If no secret is configured, auth is not enforced (unconfigured is treated as opt-out).
        """
        secret = self.config.analyze_shared_secret
        if not secret:
            return True
        supplied = self.headers.get("X-Analyze-Secret", "")
        return hmac.compare_digest(supplied, secret)

    def do_GET(self):
        if not self.require_basic_auth():
            return
        path = urlparse(self.path).path

        # Serve UI files
        if path == "/":
            self.serve_file(str(PROJECT_ROOT / "index.html"), "text/html")
        elif path == "/style.css":
            self.serve_file(str(PROJECT_ROOT / "style.css"), "text/css")
        elif path == "/app.js":
            self.serve_file(str(PROJECT_ROOT / "app.js"), "application/javascript")
        elif path == "/api/status":
            with stats_lock:
                cameras_data = {}
                for name, cam_stat in stats["cameras"].items():
                    cameras_data[name] = {
                        "pictures_analyzed": cam_stat["pictures_analyzed"],
                        "matches_found": cam_stat["matches_found"],
                        "notifications_sent": cam_stat["notifications_sent"],
                        "snooze": snooze_status(cam_stat["snooze"]),
                        "chat_id": dict(cam_stat["chat_id"]),
                        "classes": cam_stat["classes"]
                    }

                devices_data = {}
                for name, dev_stat in stats["devices"].items():
                    devices_data[name] = {
                        "snooze": snooze_status(dev_stat["snooze"]),
                        "chat_id": dict(dev_stat["chat_id"]),
                        "classes": dev_stat["classes"]
                    }

                data = {
                    "uptime": time.time() - START_TIME,
                    "global": {
                        "pictures_analyzed": stats["global"]["pictures_analyzed"],
                        "matches_found": stats["global"]["matches_found"],
                        "notifications_sent": stats["global"]["notifications_sent"],
                        "snooze": snooze_status(stats["global"]["snooze"]),
                        "chat_id": dict(stats["global"]["chat_id"]),
                        "classes": stats["global"]["classes"]
                    },
                    "cameras": cameras_data,
                    "devices": devices_data,
                    "last_detection": stats["last_detection"]
                }
            self.send_json(data)
        elif path == "/api/settings":
            self.send_json(mask_settings(self.config))
        elif path == "/api/classes":
            available = sorted(self.model.names.values()) if self.model else []
            self.send_json({"available": available, "default": self.config.classes})
        elif path.startswith("/api/last-image"):
            with stats_lock:
                image_path = stats["last_image_path"]
            if image_path and os.path.exists(image_path):
                self.serve_file(image_path, "image/jpeg")
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"No image found")
        elif path.startswith("/api/image"):
            query = urlparse(self.path).query
            params = parse_qs(query)
            camera = params.get("camera", [None])[0]
            filename = params.get("file", [None])[0]
            if not camera or not filename:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Bad Request: camera and file required")
                return
            safe_filename = os.path.basename(unquote(filename))
            camera_folder = get_camera_folder(self.config, unquote(camera))
            image_path = os.path.join(camera_folder, safe_filename)
            if os.path.exists(image_path):
                self.serve_file(image_path, "image/jpeg")
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Image not found")
        elif path.startswith("/api/history"):
            query = urlparse(self.path).query
            params = parse_qs(query)
            camera = params.get("camera", [None])[0]
            try:
                offset = max(0, int(params.get("offset", ["0"])[0]))
            except ValueError:
                offset = 0
            try:
                limit = int(params.get("limit", [str(MAX_CACHE_SIZE)])[0])
            except ValueError:
                limit = MAX_CACHE_SIZE
            limit = max(1, min(limit, 500))  # guard against pathological disk scans
            history = get_history_page(self.config, camera, offset, limit)
            self.send_json(history)
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def serve_file(self, filepath, content_type):
        try:
            with open(filepath, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"Internal Server Error")

    def send_json(self, data):
        try:
            content = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f'{{"error": "{str(e)}"}}'.encode('utf-8'))

    def do_POST(self):
        if self.path != "/analyze-image" and not self.require_basic_auth():
            return

        if self.path == "/analyze-image":
            if not self.check_shared_secret():
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b"Unauthorized")
                return

            content_type = self.headers.get('Content-Type')
            if not content_type or not content_type.startswith('multipart/form-data'):
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Bad Request: Must be multipart/form-data")
                return

            try:
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length)

                msg_bytes = f"Content-Type: {content_type}\r\n\r\n".encode('utf-8') + body
                msg = BytesParser(policy=default).parsebytes(msg_bytes)

                image_data = None
                device_name = ""
                channel_name = ""
                notify_chat = None
                silent = False
                filename = "uploaded_image.jpg"

                for part in msg.walk():
                    cd = part.get('Content-Disposition', '')
                    if not cd:
                        continue
                    params = {}
                    for param in cd.split(';'):
                        if '=' in param:
                            k, v = param.split('=', 1)
                            params[k.strip().lower()] = v.strip().strip('"')

                    name = params.get('name')
                    if name == 'image':
                        image_data = part.get_payload(decode=True)
                        filename = params.get('filename') or "uploaded_image.jpg"
                    elif name == 'device_name':
                        device_name = part.get_payload(decode=True).decode('utf-8', errors='replace').strip()
                    elif name == 'channel_name':
                        channel_name = part.get_payload(decode=True).decode('utf-8', errors='replace').strip()
                    elif name == 'notify-chat':
                        notify_chat = part.get_payload(decode=True).decode('utf-8', errors='replace').strip()
                    elif name == 'silent':
                        silent_raw = part.get_payload(decode=True).decode('utf-8', errors='replace').strip().lower()
                        silent = silent_raw in ('1', 'true', 'yes', 'on')

                if not image_data:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Bad Request: Missing image file field named 'image'")
                    return

                if not os.path.exists(self.config.download_folder):
                    os.makedirs(self.config.download_folder)

                timestamp = int(time.time())
                camera_id = f"{device_name or 'DVR'} - {channel_name or 'Camera'}"
                camera_folder = get_camera_folder(self.config, camera_id)
                os.makedirs(camera_folder, exist_ok=True)
                unique_filename = f"upload_{timestamp}_{filename}"
                filepath = os.path.join(camera_folder, unique_filename)

                with open(filepath, "wb") as f:
                    f.write(image_data)

                if is_video_file(filename):
                    result = handle_video(
                        self.config, filepath,
                        device_name=device_name or "DVR", channel_name=channel_name or "Camera",
                        chat_id=notify_chat, force_chat_id=bool(notify_chat), silent=silent
                    )
                else:
                    result = analyze_image(
                        self.config, self.model, filepath,
                        device_name=device_name or "DVR", channel_name=channel_name or "Camera",
                        chat_id=notify_chat, force_chat_id=bool(notify_chat), silent=silent
                    )

                self.send_json(result)

            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f'{{"error": "{str(e)}"}}'.encode('utf-8'))

        elif self.path == "/api/snooze":
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length).decode('utf-8')
                data = json.loads(body)
                minutes = data.get("minutes", 0)
                camera = data.get("camera", "all")
                device = data.get("device", "")
                media_type = data.get("media_type", "all")

                if media_type not in ("picture", "video", "all"):
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Bad Request: media_type must be 'picture', 'video', or 'all'")
                    return

                if minutes == "forever":
                    until_time = FOREVER_SNOOZE_UNTIL
                else:
                    minutes = float(minutes)
                    until_time = time.time() + (minutes * 60) if minutes > 0 else 0.0

                set_snooze(camera, media_type, until_time, device=device)
                save_notification_settings(self.config)

                self.send_json({"status": "success", "camera": camera, "device": device, "media_type": media_type, "snooze_until": until_time})
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f'{{"error": "{str(e)}"}}'.encode('utf-8'))

        elif self.path == "/api/notify-chat":
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length).decode('utf-8')
                data = json.loads(body)
                camera = data.get("camera", "all")
                device = data.get("device", "")
                media_type = data.get("media_type", "all")
                chat_id_value = (data.get("chat_id") or "").strip()

                if media_type not in ("picture", "video", "all"):
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Bad Request: media_type must be 'picture', 'video', or 'all'")
                    return

                set_notify_chat_id(camera, media_type, chat_id_value, device=device)
                save_notification_settings(self.config)

                self.send_json({"status": "success", "camera": camera, "device": device, "media_type": media_type, "chat_id": chat_id_value})
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f'{{"error": "{str(e)}"}}'.encode('utf-8'))

        elif self.path == "/api/target-classes":
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length).decode('utf-8')
                data = json.loads(body)
                camera = data.get("camera", "all")
                device = data.get("device", "")
                classes_value = (data.get("classes") or "").strip()

                set_target_classes(camera, classes_value, device=device)
                save_notification_settings(self.config)

                self.send_json({"status": "success", "camera": camera, "device": device, "classes": classes_value})
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f'{{"error": "{str(e)}"}}'.encode('utf-8'))

        elif self.path == "/api/trigger-analysis":
            try:
                with stats_lock:
                    last_image_path = stats["last_image_path"]
                    last_dev, last_chan = stats.get("last_image_camera") or ("DVR", "Camera")

                if not last_image_path or not os.path.exists(last_image_path):
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "No last image path available to re-analyze"}).encode('utf-8'))
                    return

                res = analyze_image(
                    self.config, self.model, last_image_path,
                    device_name=last_dev, channel_name=last_chan, chat_id=self.config.telegram_chat_id
                )
                self.send_json(res)
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f'{{"error": "{str(e)}"}}'.encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

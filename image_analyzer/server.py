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
from .stats import MAX_CACHE_SIZE, START_TIME, get_camera_stats, get_history_page, stats, stats_lock
from .utils import get_camera_folder, is_video_file, mask_settings, sanitize_component

# image_analyzer/server.py -> image_analyzer/ -> repo root, where the static dashboard
# files (index.html, style.css, app.js) live.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class UploadHandler(BaseHTTPRequestHandler):
    config = None
    model = None
    target_classes = None

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
                        "snooze_remaining": max(0, int(cam_stat["snooze_until"] - time.time()))
                    }

                data = {
                    "uptime": time.time() - START_TIME,
                    "global": {
                        "pictures_analyzed": stats["global"]["pictures_analyzed"],
                        "matches_found": stats["global"]["matches_found"],
                        "notifications_sent": stats["global"]["notifications_sent"],
                        "snooze_remaining": max(0, int(stats["global"]["snooze_until"] - time.time()))
                    },
                    "cameras": cameras_data,
                    "last_detection": stats["last_detection"]
                }
            self.send_json(data)
        elif path == "/api/settings":
            self.send_json(mask_settings(self.config))
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
            safe_camera = sanitize_component(unquote(camera))
            safe_filename = os.path.basename(unquote(filename))
            camera_folder = os.path.join(self.config.download_folder, safe_camera)
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
                        chat_id=notify_chat
                    )
                else:
                    result = analyze_image(
                        self.config, self.model, self.target_classes, filepath,
                        device_name=device_name or "DVR", channel_name=channel_name or "Camera",
                        chat_id=notify_chat
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
                minutes = int(data.get("minutes", 0))
                camera = data.get("camera", "all")

                until_time = time.time() + (minutes * 60) if minutes > 0 else 0.0

                if camera == "all":
                    with stats_lock:
                        stats["global"]["snooze_until"] = until_time
                else:
                    cam_stats = get_camera_stats(camera)
                    with stats_lock:
                        cam_stats["snooze_until"] = until_time

                self.send_json({"status": "success", "camera": camera, "snooze_until": until_time})
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
                    self.config, self.model, self.target_classes, last_image_path,
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

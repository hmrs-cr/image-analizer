# image-analizer

A single-process "unified DVR monitor": it watches for new camera snapshots from one or more
image sources (currently a Gmail/IMAP inbox that receives motion-alert emails from a DVR/NVR
system), runs local object detection ([YOLOv8](https://github.com/ultralytics/ultralytics)) and
optional face recognition ([DeepFace](https://github.com/serengil/deepface)) on each image,
optionally asks Gemini for a natural-language description, sends a Telegram photo alert, and
serves a small dashboard (static HTML/CSS/JS) plus a JSON API over the same HTTP server.

## Features

- **IMAP email source** — IDLEs on a Gmail (or other IMAP) inbox, matches DVR motion-alert
  emails, extracts device/channel names from the body, and downloads JPG attachments.
- **Local object detection** — runs YOLOv8 on every downloaded image and filters detections to a
  configurable set of COCO classes and minimum confidence.
- **Optional face recognition** — runs DeepFace against a folder of known identities when a
  person is detected.
- **Optional Gemini captions** — asks Gemini for a natural-language description of a match.
- **Telegram alerts** — sends the photo plus detection summary to a Telegram chat, with
  per-camera and global snooze support.
- **Dashboard + JSON API** — a single-page dashboard (no build step) showing live stats, last
  image, and paginated detection history, backed by a small HTTP API.
- **Pluggable sources** — new ingestion sources (FTP, Dropbox, etc.) can be added without
  touching the analysis pipeline.

## Requirements

- Python 3.11+
- An IMAP mailbox that receives the DVR's motion-alert emails (or a custom source you add)
- Optional: a Telegram bot token/chat for alerts, a Gemini API key for captions, a folder of
  reference faces for DeepFace

## Running locally

```bash
pip install -r requirements.txt
python image-analyzer-service.py [--flags or equivalent env vars]
```

There's no test suite, linter, or build step — verify changes by running the script and
exercising the relevant HTTP endpoints (`curl`, or the dashboard at `http://localhost:5000/`), or
with `python -m py_compile image_analyzer/*.py image_analyzer/sources/*.py` for a quick syntax
check.

## Running with Docker

```bash
docker compose up -d --build
```

The `Dockerfile` installs `ultralytics`, `imapclient`, `tf-keras`, `requests`, `google-genai`
(DeepFace is commented out — it's optional and heavy) and pre-bakes the YOLO model into the
image. It also expects a `security-cam.md` file (the Gemini system-prompt text) to be supplied at
deploy time — it is intentionally not part of this repo.

## Configuration

Every setting is configurable via either a CLI flag or an environment variable of the same name
(see `get_config()` in [image_analyzer/config.py](image_analyzer/config.py));
[docker-compose.yml](docker-compose.yml) wires the env var names. Only `IMAP_FOLDER` has a
built-in default (`INBOX`) that's always required — `TARGET_FROM`/`TARGET_SUBJECT` are optional
filters.

| Flag | Env var | Description |
| --- | --- | --- |
| `--imap-server` | `IMAP_SERVER` | IMAP server address (default `imap.gmail.com`) |
| `--email` | `EMAIL_ACCOUNT` | Email account address |
| `--password` | `EMAIL_PASSWORD` | Gmail App Password |
| `--from-address` | `TARGET_FROM` | Optional: only process emails sent from this address |
| `--subject` | `TARGET_SUBJECT` | Optional: only process emails containing this subject |
| `--imap-folder` | `IMAP_FOLDER` | IMAP folder to monitor (default `INBOX`) |
| `--download-folder` | `DOWNLOAD_FOLDER` | Directory to save downloaded images |
| `--model-name` | `YOLO_MODEL` | YOLO model version |
| `--facial-db` | `FACIAL_DB_PATH` | Directory path for DeepFace known identities |
| `--min-confidence` | `MIN_CONFIDENCE` | Minimum confidence threshold (0.0-1.0) |
| `--classes` | `TARGET_CLASSES` | Comma-separated COCO class names to detect |
| `--gemini-api-key` | `GEMINI_API_KEY` | Google Gemini API Key |
| `--gemini-model` | `GEMINI_MODEL` | Google Gemini model version |
| `--gemini-system-prompt-file` | `GEMINI_SYSTEM_PROMPT_FILE` | Path to a txt file containing the Gemini system prompt |
| `--gemini-timeout` | `GEMINI_TIMEOUT` | Timeout for Gemini requests in seconds |
| `--telegram-token` | `TELEGRAM_TOKEN` | Telegram Bot API Token |
| `--telegram-chat-id` | `TELEGRAM_CHAT_ID` | Telegram Chat ID to send alerts to |
| `--telegram-retries` | `TELEGRAM_RETRIES` | Retries to send the notification if it fails |
| `--host` | `HTTP_HOST` | Host for the HTTP API server |
| `--port` | `HTTP_PORT` | Port for the HTTP API server |
| `--auth-username` | `AUTH_USERNAME` | Basic Auth username for the dashboard/API |
| `--auth-password` | `AUTH_PASSWORD` | Basic Auth password for the dashboard/API |
| `--analyze-shared-secret` | `ANALYZE_SHARED_SECRET` | Shared secret for automated callers of `/analyze-image` |

Auth is opt-out: Basic Auth and the analyze shared secret are only enforced when their
corresponding credentials are configured.

## HTTP API

All routes require HTTP Basic Auth (when configured) except `/analyze-image`, which uses the
`X-Analyze-Secret` header instead.

| Method | Path | Description |
| --- | --- | --- |
| GET | `/` | Dashboard (`index.html`) |
| GET | `/api/status` | Live global/per-camera stats and last detection |
| GET | `/api/settings` | Current config, with secrets redacted |
| GET | `/api/history` | Paginated detection history (`?camera=&offset=&limit=`) |
| GET | `/api/image` | A stored image (`?camera=&file=`) |
| GET | `/api/last-image` | The most recently processed image |
| POST | `/api/snooze` | Snooze alerts globally or per camera (`{"minutes": N, "camera": "..."}`) |
| POST | `/api/trigger-analysis` | Re-run analysis on the last processed image |
| POST | `/analyze-image` | Submit an image directly for analysis (`multipart/form-data`) |

## Architecture

See [CLAUDE.md](CLAUDE.md) for a detailed breakdown of the `image_analyzer/` package layout, the
analysis pipeline, the pluggable image-source model, and the frontend's mock-mode/pagination
behavior.

## Adding a new image source

Create `image_analyzer/sources/<name>_source.py` with a class implementing the `ImageSource` ABC
(`sources/base.py`): implement a blocking `run()` loop that calls
`self.on_image(filepath, device_name, channel_name, chat_id)` for each new image, and
`is_configured(cls, config)` to decide whether it should start at all. Then add it to
`AVAILABLE_SOURCES` in `sources/__init__.py` — no other changes are needed, since sources never
import the analysis pipeline directly.

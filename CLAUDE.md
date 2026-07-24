# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-process "unified DVR monitor": it watches for new camera snapshots from one or more
image sources (currently a Gmail inbox that receives motion-alert emails from a DVR/NVR system),
runs local object detection (YOLOv8) and optional face recognition (DeepFace) on each image,
optionally asks Gemini for a natural-language description, sends a Telegram photo alert, and serves
a small dashboard (static HTML/CSS/JS) plus a JSON API over the same HTTP server.

There is no build system, package manifest, or test suite in this repo. It's a plain `image_analyzer`
Python package plus three static frontend files, run via the `image-analyzer-service.py` entrypoint
shim at the repo root.

## Running it

```bash
pip install -r requirements.txt
python image-analyzer-service.py [--flags or equivalent env vars]
```

There's no test suite, linter, or build step configured — don't invent one. Verify changes by
running the script and exercising the relevant HTTP endpoints (e.g. `curl` or the dashboard at
`http://localhost:5000/`), or with `python -m py_compile image_analyzer/*.py image_analyzer/sources/*.py`
for a quick syntax check across the whole package.

### Docker

```bash
docker compose up -d --build
```

`Dockerfile` installs `ultralytics`, `imapclient`, `tf-keras`, `requests`, `google-genai` (DeepFace
is commented out — it's optional and heavy) and pre-bakes the YOLO model into the image. It also
`COPY`s a `security-cam.md` that is **not** part of this repo (it's the Gemini system-prompt file,
supplied at deploy time) — don't be surprised it's missing locally.

Every setting is configurable via either a CLI flag or an environment variable of the same name
(see `get_config()` in `image_analyzer/config.py`); `docker-compose.yml` wires the env var names.

## Architecture

The code lives in the `image_analyzer/` package, split by concern; `image-analyzer-service.py` at
the repo root is just a shim that calls `image_analyzer.app.main()` (kept as the Docker
`ENTRYPOINT` target and so `python image-analyzer-service.py` still works unchanged).

- **`app.py`** — `main()` is the composition root: builds config, loads the YOLO model, computes
  `TARGET_CLASSES` from `--classes`, starts every configured image source (see below), wires
  `UploadHandler`'s class attributes, and runs the `ThreadingHTTPServer`.
- **`config.py`** — `get_config()`, the single argparse/env-var definition for every setting.
- **`pipeline.py`** — `analyze_image(config, model, TARGET_CLASSES, img_path, ...)` is the shared
  sink every ingestion path converges on: runs YOLO, filters detections to `TARGET_CLASSES` and
  `--min-confidence`, optionally runs DeepFace against `--facial-db` when a person is detected,
  optionally calls `gemini.get_gemini_description()` for a caption when there's a match, checks
  per-camera/global snooze state, sends a Telegram alert (`notify.send_telegram_alert`), updates
  in-memory `stats`, and persists a JSON "sidecar" file next to the image so history survives a
  restart.
- **`stats.py`** — the module-level `stats` dict + `stats_lock` (an `RLock`, since `stats` is
  mutated from both source threads and per-request HTTP handler threads), `get_camera_stats`, and
  all history/retention persistence: `save_detection_sidecar`, `load_history_from_disk` (replays
  sidecars at startup), `get_history_page` (in-memory cache fast path, falls back to scanning
  on-disk sidecars via `iter_camera_sidecars` once a page goes back further than
  `MAX_CACHE_SIZE`), and `prune_old_images` (the *only* disk-retention mechanism — deletes images
  and their sidecars past `RETENTION_SECONDS`/48h; `MAX_CACHE_SIZE` only bounds the in-memory list).
  Both `pipeline.py` and `server.py` import the same `stats`/`stats_lock` objects from here.
- **`utils.py`** — cross-cutting helpers: `sanitize_component`, `mask_settings`, `get_camera_folder`.
- **`gemini.py`** / **`notify.py`** — single-function modules for the Gemini description call and
  the Telegram photo alert, respectively.
- **`server.py`** — `UploadHandler(BaseHTTPRequestHandler)`, served via `ThreadingHTTPServer` (one
  thread per request). Implements the dashboard's static file serving (`/`, `/style.css`, `/app.js`,
  resolved relative to the repo root via `PROJECT_ROOT` since the module itself lives one directory
  deeper than before), the JSON API (`/api/status`, `/api/settings`, `/api/history`, `/api/image`,
  `/api/last-image`, `/api/snooze`, `/api/trigger-analysis`), and `POST /analyze-image` (calls
  `pipeline.analyze_image` directly — this is a request/response ingestion path, not a background
  source). All routes require HTTP Basic Auth (`--auth-username`/`--auth-password`) except
  `/analyze-image`, which uses the `X-Analyze-Secret` header instead. Both auth mechanisms are
  opt-out: if the corresponding credentials aren't configured, that check is skipped (with a
  startup warning).
- **`sources/`** — pluggable background image sources. `sources/base.py` defines the `ImageSource`
  ABC: subclasses implement `run()` (a blocking loop) and call
  `self.on_image(filepath, device_name, channel_name, chat_id)` for each new image found;
  `is_configured(cls, config)` decides whether `app.py` starts it at all. `sources/imap_source.py`'s
  `ImapEmailSource` is the current (and only) implementation — it IDLEs on the configured IMAP
  folder and, on mail matching `--from-address`/`--subject`, parses the DVR's plaintext body
  (`parse_email_body`) for device/channel names and saves JPG attachments under
  `download_folder/<sanitized camera id>/`. **To add a new source** (FTP, Dropbox, etc.): create
  `sources/<name>_source.py` with a class implementing `ImageSource`, then add it to the
  `AVAILABLE_SOURCES` list in `sources/__init__.py` — no changes needed anywhere else, since sources
  never import `pipeline.py` directly, only call the `on_image` callback `app.py` hands them.
- **Camera identity** — cameras are identified throughout by a single string key
  `"{device_name} - {channel_name}"` (e.g. `"Casa - Front Door"`), built in `pipeline.analyze_image`.
  The frontend derives a two-level grouping (home → camera) from this same string by splitting on
  `" - "` (see `getCameraTreeEntries` in `app.js`) — if you change the separator or format here, the
  dashboard grouping breaks silently.

### Frontend

`index.html` + `app.js` + `style.css` form a single-page dashboard with no build step or framework —
`app.js` is a single `DOMContentLoaded` handler that polls `/api/status` every 3s and independently
paginates `/api/history` for infinite scroll. Two things worth knowing before touching it:

- **Mock mode**: opening `index.html` directly as a `file://` URL, or appending `?mock=1`, switches
  every fetch to hardcoded mock data (`getMockStatusData`/`getMockHistoryData`/`getMockSettingsData`)
  so the UI is explorable without a running backend. Any fetch failure also silently falls back to
  mock data with an "offline" indicator — keep this in mind when debugging why the dashboard shows
  data that doesn't match the server.
- **History pagination model**: `historyData` in `app.js` mirrors a server-side page fetched from
  `/api/history?offset=&limit=`; `loadHistory()` always refetches from offset 0 (sized to cover
  what's already loaded) so a periodic refresh can pick up new detections without resetting scroll
  position, while `fetchMoreHistoryPage()` appends older pages as the user scrolls. This mirrors the
  cache/disk-fallback split in `get_history_page` on the backend.

## Security-relevant behavior to preserve

- Config values are never echoed back verbatim: `mask_settings()` redacts any config key containing
  `password`/`key`/`token`/`secret` before `/api/settings` returns it.
- `sanitize_component()` (strips to `[A-Za-z0-9_.-]`, truncated to 120 chars) is what keeps
  camera-id-derived folder names and `/api/image` query params from becoming path traversal —
  any new code deriving a filesystem path from user/email input should reuse it.
- Auth comparisons use `hmac.compare_digest` (Basic Auth credentials and the analyze shared secret)
  — don't replace with `==`.

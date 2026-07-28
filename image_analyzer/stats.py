import json
import os
import sys
import threading
import time
from urllib.parse import quote

from .utils import get_camera_folder

START_TIME = time.time()
RETENTION_SECONDS = 48 * 60 * 60
# In-memory cache size only. Disk retention is governed solely by RETENTION_SECONDS
# (see prune_old_images); this cap does not delete anything from disk.
MAX_CACHE_SIZE = 100
# Minimum time between prune_old_images runs triggered from the analyze_image hot path.
# Retention is 48h-scale, so per-image pruning granularity is unnecessary; this keeps the
# tree walk from running on every single analyzed image.
PRUNE_MIN_INTERVAL_SECONDS = 5 * 60

MEDIA_TYPES = ("picture", "video")
# Sentinel "snoozed until" timestamp for the indefinite ("Forever") snooze option. Using a
# real (if distant) timestamp rather than float('inf') keeps it a plain JSON number -- both
# in /api/status and in the on-disk notification_settings.json -- with no special-case
# (de)serialization.
FOREVER_SNOOZE_UNTIL = 4102444800.0  # 2100-01-01T00:00:00Z
NOTIFICATION_SETTINGS_FILENAME = "notification_settings.json"


def _new_snooze_state():
    return {t: 0.0 for t in MEDIA_TYPES}


def _new_chat_id_overrides():
    return {t: "" for t in MEDIA_TYPES}


stats = {
    "global": {
        "pictures_analyzed": 0,
        "matches_found": 0,
        "notifications_sent": 0,
        "snooze": _new_snooze_state(),
        "chat_id": _new_chat_id_overrides(),
        "classes": ""
    },
    "cameras": {},
    "devices": {},
    "history": {},
    "last_detection": None,
    "last_image_path": None,
    "last_image_camera": None
}
# Guards `stats`, which is mutated both by the IMAP background thread and by
# per-request HTTP handler threads (ThreadingHTTPServer spawns one per request).
stats_lock = threading.RLock()

# Guards the throttling check in maybe_prune_old_images; last run defaults to process
# start so a burst of images right after boot doesn't immediately re-trigger the prune
# app.py already ran synchronously before serving started.
_last_prune_time = START_TIME
_prune_lock = threading.Lock()


def get_camera_stats(camera_id):
    with stats_lock:
        if camera_id not in stats["cameras"]:
            stats["cameras"][camera_id] = {
                "pictures_analyzed": 0,
                "matches_found": 0,
                "notifications_sent": 0,
                "snooze": _new_snooze_state(),
                "chat_id": _new_chat_id_overrides(),
                "classes": ""
            }
        return stats["cameras"][camera_id]


def get_device_stats(device_name):
    with stats_lock:
        if device_name not in stats["devices"]:
            stats["devices"][device_name] = {
                "snooze": _new_snooze_state(),
                "chat_id": _new_chat_id_overrides(),
                "classes": ""
            }
        return stats["devices"][device_name]


def is_snoozed(camera_id, device_name, media_type):
    """Whether notifications for this camera/media_type are currently snoozed, checking
    the per-camera, per-device, and global snooze settings -- any one of the three being
    active is enough to snooze."""
    now = time.time()
    with stats_lock:
        cam_snooze = get_camera_stats(camera_id)["snooze"][media_type]
        device_snooze = get_device_stats(device_name)["snooze"][media_type]
        global_snooze = stats["global"]["snooze"][media_type]
    return now < cam_snooze or now < device_snooze or now < global_snooze


def _resolve_scope_stats(camera, device):
    """Shared scope resolution for set_snooze/set_notify_chat_id: a non-empty `device`
    targets that device, else a `camera` other than 'all' targets that camera, else the
    global setting."""
    if device:
        return get_device_stats(device)
    if camera and camera != "all":
        return get_camera_stats(camera)
    return None


def set_snooze(camera, media_type, until_time, device=""):
    """Sets snooze_until for `device` (device-level), else `camera` ('all' for the global
    setting), and `media_type` ('picture', 'video', or 'all' for both). Returns the updated
    snooze sub-dict."""
    types = MEDIA_TYPES if media_type == "all" else (media_type,)
    for t in types:
        if t not in MEDIA_TYPES:
            raise ValueError(f"Unknown media_type: {media_type}")
    with stats_lock:
        scoped = _resolve_scope_stats(camera, device)
        target = scoped["snooze"] if scoped is not None else stats["global"]["snooze"]
        for t in types:
            target[t] = until_time
        return dict(target)


def snooze_status(snooze_dict):
    """Renders a snooze sub-dict ({"picture": until, "video": until}) into the
    remaining-seconds/forever shape the dashboard consumes."""
    now = time.time()
    status = {}
    for t in MEDIA_TYPES:
        until = snooze_dict.get(t, 0.0)
        status[t] = {
            "remaining": max(0, int(until - now)) if until > now else 0,
            "forever": until >= FOREVER_SNOOZE_UNTIL
        }
    return status


def set_notify_chat_id(camera, media_type, chat_id_value, device=""):
    """Sets the notification chat-ID override for `device` (device-level), else `camera`
    ('all' for the global setting), and `media_type` ('picture', 'video', or 'all' for
    both). An empty string clears the override, falling back to resolve_notify_chat_id's
    lower-priority sources. Returns the updated chat_id sub-dict."""
    types = MEDIA_TYPES if media_type == "all" else (media_type,)
    for t in types:
        if t not in MEDIA_TYPES:
            raise ValueError(f"Unknown media_type: {media_type}")
    with stats_lock:
        scoped = _resolve_scope_stats(camera, device)
        target = scoped["chat_id"] if scoped is not None else stats["global"]["chat_id"]
        for t in types:
            target[t] = chat_id_value
        return dict(target)


def resolve_notify_chat_id(camera_id, device_name, media_type, fallback_chat_id, config):
    """Resolves the Telegram chat ID to notify, in priority order: a per-camera override
    for this media_type, then a per-device override, then a global override, then whatever
    the caller passed in (e.g. a source's own default or a per-request override), then
    finally the configured --telegram-chat-id/TELEGRAM_CHAT_ID default."""
    with stats_lock:
        cam_override = get_camera_stats(camera_id)["chat_id"][media_type]
        device_override = get_device_stats(device_name)["chat_id"][media_type]
        global_override = stats["global"]["chat_id"][media_type]
    return cam_override or device_override or global_override or fallback_chat_id or config.telegram_chat_id


def set_target_classes(camera, classes_value, device=""):
    """Sets the YOLO target-classes override (a comma-separated class-name string) for
    `device` (device-level), else `camera` ('all' for the global setting). An empty string
    clears the override, falling back to resolve_target_classes's lower-priority sources."""
    with stats_lock:
        scoped = _resolve_scope_stats(camera, device)
        if scoped is not None:
            scoped["classes"] = classes_value
        else:
            stats["global"]["classes"] = classes_value
        return classes_value


def resolve_target_classes(camera_id, device_name, config):
    """Resolves the comma-separated YOLO class-name string to detect against, in priority
    order: a per-camera override, then a per-device override, then a global override, then
    finally the configured --classes/TARGET_CLASSES default."""
    with stats_lock:
        cam_override = get_camera_stats(camera_id)["classes"]
        device_override = get_device_stats(device_name)["classes"]
        global_override = stats["global"]["classes"]
    return cam_override or device_override or global_override or config.classes


def notification_settings_path(config):
    return os.path.join(config.download_folder, NOTIFICATION_SETTINGS_FILENAME)


def save_notification_settings(config):
    """Persists global + per-camera/device snooze, chat-ID-override, and target-classes
    settings so they survive a restart."""
    if not config.download_folder:
        return
    with stats_lock:
        state = {
            "global": {
                "snooze": dict(stats["global"]["snooze"]),
                "chat_id": dict(stats["global"]["chat_id"]),
                "classes": stats["global"]["classes"]
            },
            "cameras": {
                cam_id: {"snooze": dict(cam["snooze"]), "chat_id": dict(cam["chat_id"]), "classes": cam["classes"]}
                for cam_id, cam in stats["cameras"].items()
            },
            "devices": {
                device_name: {"snooze": dict(dev["snooze"]), "chat_id": dict(dev["chat_id"]), "classes": dev["classes"]}
                for device_name, dev in stats["devices"].items()
            }
        }
    try:
        os.makedirs(config.download_folder, exist_ok=True)
        path = notification_settings_path(config)
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp_path, path)  # atomic, so a crash mid-write can't corrupt the saved state
    except Exception as e:
        print(f"Failed to save notification settings: {e}", file=sys.stderr)


def load_notification_settings(config):
    """Restores global + per-camera snooze and chat-ID-override settings saved by
    save_notification_settings.

    Call after load_history_from_disk so per-camera stats dicts already exist for every
    camera with history; this only ever overlays the snooze/chat_id sub-dicts on top of them.
    """
    path = notification_settings_path(config)
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception as e:
        print(f"Failed to load notification settings: {e}", file=sys.stderr)
        return

    with stats_lock:
        global_state = state.get("global", {})
        for t in MEDIA_TYPES:
            if t in global_state.get("snooze", {}):
                stats["global"]["snooze"][t] = global_state["snooze"][t]
            if t in global_state.get("chat_id", {}):
                stats["global"]["chat_id"][t] = global_state["chat_id"][t]
        if "classes" in global_state:
            stats["global"]["classes"] = global_state["classes"]

        for camera_id, cam_state in state.get("cameras", {}).items():
            cam = get_camera_stats(camera_id)
            for t in MEDIA_TYPES:
                if t in cam_state.get("snooze", {}):
                    cam["snooze"][t] = cam_state["snooze"][t]
                if t in cam_state.get("chat_id", {}):
                    cam["chat_id"][t] = cam_state["chat_id"][t]
            if "classes" in cam_state:
                cam["classes"] = cam_state["classes"]

        for device_name, dev_state in state.get("devices", {}).items():
            dev = get_device_stats(device_name)
            for t in MEDIA_TYPES:
                if t in dev_state.get("snooze", {}):
                    dev["snooze"][t] = dev_state["snooze"][t]
                if t in dev_state.get("chat_id", {}):
                    dev["chat_id"][t] = dev_state["chat_id"][t]
            if "classes" in dev_state:
                dev["classes"] = dev_state["classes"]

    print("Restored notification settings from disk.", file=sys.stderr)


def prune_old_images(config, now=None):
    """Delete image files older than 24 hours from the download tree."""
    if not config.download_folder or not os.path.isdir(config.download_folder):
        return 0

    cutoff = (now or time.time()) - RETENTION_SECONDS
    deleted_count = 0

    for root, _, files in os.walk(config.download_folder):
        for filename in files:
            path = os.path.join(root, filename)
            try:
                if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    deleted_count += 1
            except Exception as exc:
                print(f"Failed to prune old image {path}: {exc}", file=sys.stderr)

    if deleted_count:
        print(f"Pruned {deleted_count} image(s) older than 24 hours.", file=sys.stderr)
    return deleted_count


def maybe_prune_old_images(config):
    """Runs prune_old_images in a background thread, throttled to at most once every
    PRUNE_MIN_INTERVAL_SECONDS. analyze_image is the shared sink for every incoming image
    (IMAP thread and the /analyze-image HTTP handler alike), so calling prune_old_images
    from there directly would walk and stat the entire download tree on every single
    image; the cost scales with disk history, not with the image being analyzed.
    """
    global _last_prune_time
    with _prune_lock:
        now = time.time()
        if now - _last_prune_time < PRUNE_MIN_INTERVAL_SECONDS:
            return
        _last_prune_time = now
    threading.Thread(target=prune_old_images, args=(config,), daemon=True).start()


def sidecar_path_for(img_path):
    return f"{img_path}.json"


def save_detection_sidecar(img_path, entry):
    """Writes the detection entry to a JSON file next to the image so history survives a restart."""
    try:
        with open(sidecar_path_for(img_path), "w", encoding="utf-8") as f:
            json.dump(entry, f)
    except Exception as e:
        print(f"Failed to save detection sidecar for {img_path}: {e}", file=sys.stderr)


def iter_camera_sidecars(camera_folder):
    """Lists (mtime, img_path, sidecar_path) for every stored detection in a camera folder,
    newest-first by sidecar file write time, without parsing any JSON bodies.

    The sidecar is written once and never touched again, so its mtime is effectively its
    creation time; this is cheap enough to run over a whole folder just to determine order,
    deferring the actual JSON parse to only the entries a page actually needs.
    """
    if not os.path.isdir(camera_folder):
        return []
    records = []
    try:
        scanned = os.scandir(camera_folder)
    except OSError:
        return []
    with scanned:
        for item in scanned:
            if not item.name.endswith(".json") or not item.is_file(follow_symlinks=False):
                continue
            img_path = item.path[:-len(".json")]
            if not os.path.exists(img_path):
                continue  # image was pruned by retention; drop the orphaned sidecar
            try:
                mtime = item.stat().st_mtime
            except OSError:
                continue
            records.append((mtime, img_path, item.path))
    records.sort(key=lambda r: r[0], reverse=True)
    return records


def load_sidecar_entry(sidecar_path):
    """Parses one sidecar JSON file into the same shape used by the in-memory history cache."""
    try:
        with open(sidecar_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Failed to load detection sidecar {sidecar_path}: {e}", file=sys.stderr)
        return None
    camera_id = data.get("camera_id", "Unknown")
    return {
        "timestamp": data["timestamp"],
        "camera_id": camera_id,
        "location": data.get("location", ""),
        "objects": data.get("objects", []),
        "description": data.get("description", ""),
        "image_filename": data["image_filename"],
        "image_url": f"/api/image?camera={quote(camera_id)}&file={quote(data['image_filename'])}"
    }


def get_history_page(config, camera, offset, limit):
    """Returns a page of history entries, newest-first.

    Recent entries are served straight from the in-memory cache (fast path, at most
    MAX_CACHE_SIZE per camera). Once a page reaches further back than the cache holds,
    falls back to scanning the on-disk sidecars (ordered by file write time) so infinite
    scroll can keep paging through everything still on disk within RETENTION_SECONDS,
    not just the hot cache. The cache is always a subset of what's on disk, so the disk
    path alone is sufficient once a request outgrows it.
    """
    end = offset + limit
    with stats_lock:
        if camera:
            cached = list(stats["history"].get(camera, []))
        else:
            cached = []
            for entries in stats["history"].values():
                cached.extend(entries)
            cached.sort(key=lambda e: e["timestamp"], reverse=True)

    if end <= len(cached):
        return cached[offset:end]

    if camera:
        camera_folders = [get_camera_folder(config, camera)]
    else:
        # Camera folders are nested two levels deep (download_folder/<device>/<channel>),
        # matching get_camera_folder, so collect the leaf (channel) directories.
        root = config.download_folder
        camera_folders = []
        if root and os.path.isdir(root):
            for device_name in os.listdir(root):
                device_path = os.path.join(root, device_name)
                if not os.path.isdir(device_path):
                    continue
                for channel_name in os.listdir(device_path):
                    channel_path = os.path.join(device_path, channel_name)
                    if os.path.isdir(channel_path):
                        camera_folders.append(channel_path)

    disk_records = []
    for folder in camera_folders:
        disk_records.extend(iter_camera_sidecars(folder))
    disk_records.sort(key=lambda r: r[0], reverse=True)

    page = []
    for _, _img_path, sidecar_path in disk_records[offset:end]:
        parsed = load_sidecar_entry(sidecar_path)
        if parsed:
            page.append(parsed)
    return page


def load_history_from_disk(config):
    """Rebuilds in-memory stats/history from the JSON sidecars saved next to each image.

    Runs once at startup so a restart resumes with the same counts and recent-image
    history it had at shutdown, instead of starting from zero.
    """
    if not config.download_folder or not os.path.isdir(config.download_folder):
        return

    by_camera = {}
    loaded = 0
    for root, _, files in os.walk(config.download_folder):
        for filename in files:
            if not filename.endswith(".json"):
                continue
            sidecar_path = os.path.join(root, filename)
            img_path = sidecar_path[:-len(".json")]
            if not os.path.exists(img_path):
                continue  # image was pruned; drop the orphaned sidecar

            try:
                with open(sidecar_path, "r", encoding="utf-8") as f:
                    entry = json.load(f)
            except Exception as e:
                print(f"Failed to load detection sidecar {sidecar_path}: {e}", file=sys.stderr)
                continue

            entry["_image_path"] = img_path
            camera_id = entry.get("camera_id", "Unknown")
            by_camera.setdefault(camera_id, []).append(entry)
            loaded += 1

    if not loaded:
        return

    last_match = None
    for camera_id, entries in by_camera.items():
        entries.sort(key=lambda e: e["timestamp"], reverse=True)

        # Stats reflect everything still on disk (within RETENTION_SECONDS); only the
        # in-memory history list itself is capped to MAX_CACHE_SIZE below.
        cam_stats = get_camera_stats(camera_id)
        for entry in entries:
            cam_stats["pictures_analyzed"] += 1
            stats["global"]["pictures_analyzed"] += 1
            if entry.get("is_match"):
                cam_stats["matches_found"] += 1
                stats["global"]["matches_found"] += 1
            if entry.get("notified"):
                cam_stats["notifications_sent"] += 1
                stats["global"]["notifications_sent"] += 1
            if entry.get("is_match") and (not last_match or entry["timestamp"] > last_match["timestamp"]):
                last_match = entry

        stats["history"][camera_id] = [
            {
                "timestamp": e["timestamp"],
                "camera_id": e["camera_id"],
                "location": e.get("location", ""),
                "objects": e.get("objects", []),
                "description": e.get("description", ""),
                "image_filename": e["image_filename"],
                "image_url": f"/api/image?camera={quote(camera_id)}&file={quote(e['image_filename'])}"
            }
            for e in entries[:MAX_CACHE_SIZE]
        ]

    if last_match:
        device_name, _, channel_name = last_match["camera_id"].partition(" - ")
        channel_name = channel_name or "Camera"
        stats["last_detection"] = {
            "timestamp": last_match["timestamp"],
            "location": last_match.get("location", ""),
            "description": last_match.get("description", ""),
            "objects": last_match.get("objects", []),
            "camera_id": last_match["camera_id"],
            "image_filename": last_match["image_filename"],
            "image_path": last_match["_image_path"]
        }
        stats["last_image_path"] = last_match["_image_path"]
        stats["last_image_camera"] = (device_name, channel_name)

    print(f"Loaded {loaded} historical detection(s) across {len(by_camera)} camera(s) from disk.", file=sys.stderr)

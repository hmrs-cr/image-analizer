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

stats = {
    "global": {
        "pictures_analyzed": 0,
        "matches_found": 0,
        "notifications_sent": 0,
        "snooze_until": 0.0
    },
    "cameras": {},
    "history": {},
    "last_detection": None,
    "last_image_path": None,
    "last_image_camera": None
}
# Guards `stats`, which is mutated both by the IMAP background thread and by
# per-request HTTP handler threads (ThreadingHTTPServer spawns one per request).
stats_lock = threading.RLock()


def get_camera_stats(camera_id):
    with stats_lock:
        if camera_id not in stats["cameras"]:
            stats["cameras"][camera_id] = {
                "pictures_analyzed": 0,
                "matches_found": 0,
                "notifications_sent": 0,
                "snooze_until": 0.0
            }
        return stats["cameras"][camera_id]


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
        root = config.download_folder
        camera_folders = []
        if root and os.path.isdir(root):
            camera_folders = [
                os.path.join(root, name) for name in os.listdir(root)
                if os.path.isdir(os.path.join(root, name))
            ]

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

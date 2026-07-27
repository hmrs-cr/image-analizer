import os
import sys
import time
from urllib.parse import quote

try:
    from deepface import DeepFace
    DEEPFACE_AVAILABLE = True
except ImportError:
    DEEPFACE_AVAILABLE = False

from .gemini import get_gemini_description
from .notify import send_telegram_alert, send_telegram_video
from .stats import (
    MAX_CACHE_SIZE,
    get_camera_stats,
    is_snoozed,
    maybe_prune_old_images,
    resolve_notify_chat_id,
    save_detection_sidecar,
    stats,
    stats_lock,
)


def handle_video(config, video_path, device_name="DVR", channel_name="Camera", chat_id=None):
    """Relays a video clip (e.g. an mp4 motion-alert attachment) straight to Telegram.

    YOLO/DeepFace/Gemini only operate on still images, so videos skip the analysis
    pipeline entirely and are just forwarded as a notification when a chat ID is configured.
    """
    maybe_prune_old_images(config)
    print(f"Received video: {video_path}", file=sys.stderr)

    if " " in device_name and channel_name == "Camera":
        parts = device_name.split(" ", 1)
        if len(parts) == 2:
            channel_name = parts[0]
            device_name = parts[1]

    camera_id = f"{device_name} - {channel_name}"
    location_context = f"{channel_name} {device_name}"
    caption = f"*Video* en *{location_context}*"

    cam_stats = get_camera_stats(camera_id)
    effective_chat_id = resolve_notify_chat_id(camera_id, "video", chat_id, config)

    notified = False
    if is_snoozed(camera_id, "video"):
        print(f"Video notifications are currently snoozed for camera {camera_id}. Bypassing Telegram notification.", file=sys.stderr)
    elif effective_chat_id:
        notified = send_telegram_video(config, video_path, caption, effective_chat_id)
        if notified:
            with stats_lock:
                stats["global"]["notifications_sent"] += 1
                cam_stats["notifications_sent"] += 1
    else:
        print("Telegram notification bypassed: no chat ID configured.", file=sys.stderr)

    return {"video": True, "notified": notified}


def analyze_image(config, model, TARGET_CLASSES, img_path, device_name="DVR", channel_name="Camera", chat_id=None):
    """Executes local AI pipeline on a downloaded image and returns match results.

    This is the single sink every image source (IMAP, HTTP upload, ...) converges on.
    """
    maybe_prune_old_images(config)
    print(f"Analyzing image: {img_path}", file=sys.stderr)

    # Handle older string positional args safely
    if " " in device_name and channel_name == "Camera":
        parts = device_name.split(" ", 1)
        if len(parts) == 2:
            channel_name = parts[0]
            device_name = parts[1]

    camera_id = f"{device_name} - {channel_name}"
    location_context = f"{channel_name} {device_name}"

    # Update stats
    cam_stats = get_camera_stats(camera_id)
    with stats_lock:
        stats["global"]["pictures_analyzed"] += 1
        cam_stats["pictures_analyzed"] += 1

    results = model(img_path, verbose=False)

    detected_objects = []
    detections_list = []
    person_detected = False
    max_confidence = 0.0

    for result in results:
        for box in result.boxes:
            class_id = int(box.cls[0])
            confidence = float(box.conf[0])
            if class_id in TARGET_CLASSES:
                class_name = model.names[class_id]
                detected_objects.append(f"{class_name} ({confidence:.2f})")
                if confidence > max_confidence:
                    max_confidence = confidence
                if class_id == 0:
                    person_detected = True

                detections_list.append({
                    "class": class_name,
                    "confidence": confidence
                })

    recognized_names = []
    if person_detected and DEEPFACE_AVAILABLE:
        if config.facial_db and os.path.exists(config.facial_db):
            print(f"Using DeepFace database at {config.facial_db}", file=sys.stderr)
            try:
                df_results = DeepFace.find(img_path=img_path, db_path=config.facial_db, model_name="ArcFace", enforce_detection=False, silent=True)
                for face_df in df_results:
                    if not face_df.empty:
                        name = os.path.basename(os.path.dirname(face_df.iloc[0]['identity']))
                        if name not in recognized_names: recognized_names.append(name)
            except Exception as e:
                print(f"Facial recognition warning: {e}", file=sys.stderr)

    if recognized_names:
        for det in detections_list:
            if det["class"] == "person":
                det["person_name"] = recognized_names[0] if len(recognized_names) == 1 else ", ".join(recognized_names)

    is_match = bool(detected_objects and max_confidence >= config.min_confidence)
    gemini_desc = ""
    first_match_clean = ""
    caption = ""
    if is_match:
        with stats_lock:
            stats["global"]["matches_found"] += 1
            cam_stats["matches_found"] += 1
        targets_summary = ", ".join(detected_objects)
        first_match_raw = detected_objects[0].split(" ")[0]
        first_match_clean = first_match_raw.capitalize()
        caption = ""

        print(f"Match found: {targets_summary}", file=sys.stderr)

        # Multimodal integration pass with variables injected
        if config.gemini_api_key:
            print("Requesting description from Gemini...", file=sys.stderr)
            gemini_desc = get_gemini_description(config, img_path, location_context, first_match_clean)
            if gemini_desc:
                caption += f"{gemini_desc}\n"

        caption += f"*{first_match_clean}* en *{location_context}*"
        if recognized_names:
            caption += f"\n*Identified:* {', '.join(recognized_names)}"

    # Determine snooze/notification outcome before persisting the entry, so the sidecar
    # and in-memory history record whether an alert actually went out.
    snoozed = is_snoozed(camera_id, "picture")
    effective_chat_id = resolve_notify_chat_id(camera_id, "picture", chat_id, config)

    notified = False
    if snoozed:
        print(f"Notifications are currently snoozed for camera {camera_id}. Bypassing Telegram notification.", file=sys.stderr)
    elif effective_chat_id and is_match:
        notified = send_telegram_alert(config, img_path, caption, effective_chat_id)
        if notified:
            with stats_lock:
                stats["global"]["notifications_sent"] += 1
                cam_stats["notifications_sent"] += 1
    elif effective_chat_id and not is_match:
        print("No relevant targets detected; skipping Telegram notification.", file=sys.stderr)
    else:
        print("Telegram notification bypassed: no chat ID configured.", file=sys.stderr)

    if not is_match:
        print("No relevant targets detected or confidence below threshold.", file=sys.stderr)

    image_filename = os.path.basename(img_path)
    entry = {
        "timestamp": time.time(),
        "camera_id": camera_id,
        "location": location_context,
        "objects": detections_list,
        "description": gemini_desc,
        "image_filename": image_filename,
        "is_match": is_match,
        "notified": notified
    }
    save_detection_sidecar(img_path, entry)

    with stats_lock:
        if is_match:
            stats["last_detection"] = {
                "timestamp": entry["timestamp"],
                "location": location_context,
                "description": gemini_desc,
                "objects": detections_list,
                "camera_id": camera_id,
                "image_filename": image_filename,
                "image_path": img_path
            }
            stats["last_image_path"] = img_path
            stats["last_image_camera"] = (device_name, channel_name)

        camera_history = stats["history"].setdefault(camera_id, [])
        camera_history.insert(0, dict(entry, image_url=f"/api/image?camera={quote(camera_id)}&file={quote(image_filename)}"))
        # Evict from the in-memory cache only; the image/sidecar stay on disk until
        # prune_old_images removes them once they age past RETENTION_SECONDS.
        del camera_history[MAX_CACHE_SIZE:]

    return {
        "description": gemini_desc,
        "objects": detections_list
    }

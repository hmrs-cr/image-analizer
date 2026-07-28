import os
import sys
import time
from urllib.parse import quote

import cv2

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
    resolve_target_classes,
    save_detection_sidecar,
    stats,
    stats_lock,
)


def _class_names_to_ids(model, classes_csv):
    """Converts a comma-separated class-name string (e.g. config.classes or a
    resolve_target_classes override) into the set of YOLO class IDs recognized by `model`,
    dropping any names the model doesn't know about."""
    name_to_id = {name.lower(): cid for cid, name in model.names.items()}
    return {name_to_id[n.strip().lower()] for n in classes_csv.split(",") if n.strip() and n.strip().lower() in name_to_id}


def _parse_detections(model, TARGET_CLASSES, boxes):
    """Filters one YOLO Results.boxes iterable down to TARGET_CLASSES. Returns
    (detected_objects, detections_list, person_detected, max_confidence) for that one
    frame/image."""
    detected_objects = []
    detections_list = []
    person_detected = False
    max_confidence = 0.0
    for box in boxes:
        class_id = int(box.cls[0])
        confidence = float(box.conf[0])
        if class_id in TARGET_CLASSES:
            class_name = model.names[class_id]
            detected_objects.append(f"{class_name} ({confidence:.2f})")
            if confidence > max_confidence:
                max_confidence = confidence
            if class_id == 0:
                person_detected = True
            detections_list.append({"class": class_name, "confidence": confidence})
    return detected_objects, detections_list, person_detected, max_confidence


def _detect_image(model, TARGET_CLASSES, img_path):
    """Runs YOLO on a single still image. Returns (detected_objects, detections_list,
    person_detected, max_confidence)."""
    detected_objects, detections_list, person_detected, max_confidence = [], [], False, 0.0
    for result in model(img_path, verbose=False):
        objects, detections, person, confidence = _parse_detections(model, TARGET_CLASSES, result.boxes)
        detected_objects.extend(objects)
        detections_list.extend(detections)
        person_detected = person_detected or person
        max_confidence = max(max_confidence, confidence)
    return detected_objects, detections_list, person_detected, max_confidence


def _detect_video_best_frame(model, TARGET_CLASSES, video_path, vid_stride):
    """Samples frames from `video_path` (every vid_stride-th frame, via YOLO's built-in
    video decoding) and returns the detection results for whichever sampled frame had the
    single highest-confidence target-class detection -- summarizing the clip by its most
    representative frame, the same way a still image summarizes itself.

    Falls back to the first sampled frame (with whatever it detected, possibly nothing) so
    a thumbnail is always available even when no frame matches. Returns (detected_objects,
    detections_list, person_detected, max_confidence, frame) where `frame` is the raw BGR
    numpy array of the chosen frame, or None if the video couldn't be decoded at all.
    """
    best = None
    for result in model(video_path, stream=True, vid_stride=vid_stride, verbose=False):
        objects, detections, person, confidence = _parse_detections(model, TARGET_CLASSES, result.boxes)
        if best is None or confidence > best[3]:
            best = (objects, detections, person, confidence, result.orig_img)
    return best if best is not None else ([], [], False, 0.0, None)


def _recognize_faces(config, image_path):
    """Runs DeepFace against config.facial_db for a still image (or an extracted video
    frame) and returns the list of recognized identity names, if any."""
    recognized_names = []
    if not DEEPFACE_AVAILABLE or not config.facial_db or not os.path.exists(config.facial_db):
        return recognized_names
    print(f"Using DeepFace database at {config.facial_db}", file=sys.stderr)
    try:
        df_results = DeepFace.find(img_path=image_path, db_path=config.facial_db, model_name="ArcFace", enforce_detection=False, silent=True)
        for face_df in df_results:
            if not face_df.empty:
                name = os.path.basename(os.path.dirname(face_df.iloc[0]['identity']))
                if name not in recognized_names:
                    recognized_names.append(name)
    except Exception as e:
        print(f"Facial recognition warning: {e}", file=sys.stderr)
    return recognized_names


def _apply_face_names(detections_list, recognized_names):
    if not recognized_names:
        return
    for det in detections_list:
        if det["class"] == "person":
            det["person_name"] = recognized_names[0] if len(recognized_names) == 1 else ", ".join(recognized_names)


def _build_match_caption(config, image_path, detected_objects, location_context, recognized_names):
    """Builds the Telegram caption (and, if configured, a Gemini description) for a match.
    Returns (gemini_desc, caption)."""
    targets_summary = ", ".join(detected_objects)
    first_match_raw = detected_objects[0].split(" ")[0]
    first_match_clean = first_match_raw.capitalize()

    print(f"Match found: {targets_summary}", file=sys.stderr)

    gemini_desc = ""
    if config.gemini_api_key:
        print("Requesting description from Gemini...", file=sys.stderr)
        gemini_desc = get_gemini_description(config, image_path, location_context, first_match_clean)

    caption = f"{gemini_desc}\n" if gemini_desc else ""
    caption += f"*{first_match_clean}* en *{location_context}*"
    if recognized_names:
        caption += f"\n*Identified:* {', '.join(recognized_names)}"

    return gemini_desc, caption


def _maybe_notify(config, camera_id, device_name, media_type, chat_id, force_chat_id, silent, is_match, cam_stats, send_fn):
    """Resolves snooze + chat-ID overrides for (camera_id, device_name, media_type), then
    calls send_fn(effective_chat_id) if nothing suppresses the notification. Returns
    whether a notification was actually sent."""
    snoozed = is_snoozed(camera_id, device_name, media_type)
    effective_chat_id = chat_id if force_chat_id else resolve_notify_chat_id(camera_id, device_name, media_type, chat_id, config)
    label = media_type.capitalize()

    notified = False
    if silent:
        print(f"{label} notification explicitly silenced for camera {camera_id}.", file=sys.stderr)
    elif snoozed:
        print(f"{label} notifications are currently snoozed for camera {camera_id}. Bypassing Telegram notification.", file=sys.stderr)
    elif effective_chat_id and is_match:
        notified = send_fn(effective_chat_id)
        if notified:
            with stats_lock:
                stats["global"]["notifications_sent"] += 1
                cam_stats["notifications_sent"] += 1
    elif effective_chat_id and not is_match:
        print("No relevant targets detected; skipping Telegram notification.", file=sys.stderr)
    else:
        print("Telegram notification bypassed: no chat ID configured.", file=sys.stderr)
    return notified


def _persist_detection(camera_id, device_name, channel_name, location_context, image_path, image_filename,
                        detections_list, gemini_desc, is_match, notified, extra_fields=None):
    """Writes the detection sidecar and updates in-memory stats/history/last_detection --
    shared tail for both picture and video analysis."""
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
    if extra_fields:
        entry.update(extra_fields)
    save_detection_sidecar(image_path, entry)

    with stats_lock:
        if is_match:
            stats["last_detection"] = {
                "timestamp": entry["timestamp"],
                "location": location_context,
                "description": gemini_desc,
                "objects": detections_list,
                "camera_id": camera_id,
                "image_filename": image_filename,
                "image_path": image_path
            }
            stats["last_image_path"] = image_path
            stats["last_image_camera"] = (device_name, channel_name)

        camera_history = stats["history"].setdefault(camera_id, [])
        camera_history.insert(0, dict(entry, image_url=f"/api/image?camera={quote(camera_id)}&file={quote(image_filename)}"))
        # Evict from the in-memory cache only; the image/sidecar stay on disk until
        # prune_old_images removes them once they age past RETENTION_SECONDS.
        del camera_history[MAX_CACHE_SIZE:]


def _split_device_channel(device_name, channel_name):
    """Handles older string positional args safely: some callers historically passed a
    single "Channel Device" string as device_name with channel_name left at its default."""
    if " " in device_name and channel_name == "Camera":
        parts = device_name.split(" ", 1)
        if len(parts) == 2:
            return parts[1], parts[0]
    return device_name, channel_name


def handle_video(config, model, video_path, device_name="DVR", channel_name="Camera", chat_id=None, force_chat_id=False, silent=False):
    """Runs YOLO on sampled frames of a video clip (e.g. an mp4 motion-alert attachment),
    summarizing it by its single most representative frame -- the same way analyze_image
    summarizes a still image -- then optionally runs DeepFace/Gemini on that frame, relays
    the original clip to Telegram with an AI-enriched caption, and persists a sidecar plus
    the representative-frame thumbnail so video detections show up in history/dashboard
    exactly like picture detections do.

    `force_chat_id` makes `chat_id` win outright instead of being just the lowest-priority
    fallback in resolve_notify_chat_id -- used by the /analyze-image HTTP endpoint, where an
    explicit per-request chat ID should override any configured camera/global chat_id.
    `silent` unconditionally suppresses the Telegram notification (analysis/history still run).
    """
    maybe_prune_old_images(config)
    print(f"Analyzing video: {video_path}", file=sys.stderr)

    device_name, channel_name = _split_device_channel(device_name, channel_name)
    camera_id = f"{device_name} - {channel_name}"
    location_context = f"{channel_name} {device_name}"

    cam_stats = get_camera_stats(camera_id)
    with stats_lock:
        stats["global"]["pictures_analyzed"] += 1
        cam_stats["pictures_analyzed"] += 1

    classes_csv = resolve_target_classes(camera_id, device_name, config)
    TARGET_CLASSES = _class_names_to_ids(model, classes_csv)

    detected_objects, detections_list, person_detected, max_confidence, frame = _detect_video_best_frame(
        model, TARGET_CLASSES, video_path, config.video_frame_stride
    )

    if frame is None:
        # Corrupt/undecodable clip: fall back to relaying the raw video, same as before
        # YOLO analysis existed, since there's nothing to analyze or show a thumbnail for.
        print(f"Could not decode any frames from {video_path}; relaying without analysis.", file=sys.stderr)
        notified = _maybe_notify(
            config, camera_id, device_name, "video", chat_id, force_chat_id, silent, True, cam_stats,
            lambda effective_chat_id: send_telegram_video(config, video_path, f"*Video* en *{location_context}*", effective_chat_id)
        )
        return {"video": True, "notified": notified}

    frame_filename = f"{os.path.splitext(os.path.basename(video_path))[0]}_frame.jpg"
    frame_path = os.path.join(os.path.dirname(video_path), frame_filename)
    cv2.imwrite(frame_path, frame)

    recognized_names = _recognize_faces(config, frame_path) if person_detected else []
    _apply_face_names(detections_list, recognized_names)

    is_match = bool(detected_objects and max_confidence >= config.min_confidence)
    gemini_desc, caption = "", f"*Video* en *{location_context}*"
    if is_match:
        with stats_lock:
            stats["global"]["matches_found"] += 1
            cam_stats["matches_found"] += 1
        gemini_desc, caption = _build_match_caption(config, frame_path, detected_objects, location_context, recognized_names)
    else:
        print("No relevant targets detected or confidence below threshold.", file=sys.stderr)

    notified = _maybe_notify(
        config, camera_id, device_name, "video", chat_id, force_chat_id, silent, is_match, cam_stats,
        lambda effective_chat_id: send_telegram_video(config, video_path, caption, effective_chat_id)
    )

    _persist_detection(
        camera_id, device_name, channel_name, location_context, frame_path, frame_filename,
        detections_list, gemini_desc, is_match, notified,
        extra_fields={"is_video": True, "video_filename": os.path.basename(video_path)}
    )

    return {"description": gemini_desc, "objects": detections_list}


def analyze_image(config, model, img_path, device_name="DVR", channel_name="Camera", chat_id=None, force_chat_id=False, silent=False):
    """Executes local AI pipeline on a downloaded image and returns match results.

    This is the single sink every image source (IMAP, HTTP upload, ...) converges on.

    `force_chat_id` makes `chat_id` win outright instead of being just the lowest-priority
    fallback in resolve_notify_chat_id -- used by the /analyze-image HTTP endpoint, where an
    explicit per-request chat ID should override any configured camera/global chat_id.
    `silent` unconditionally suppresses the Telegram notification (analysis/history still run).
    """
    maybe_prune_old_images(config)
    print(f"Analyzing image: {img_path}", file=sys.stderr)

    device_name, channel_name = _split_device_channel(device_name, channel_name)
    camera_id = f"{device_name} - {channel_name}"
    location_context = f"{channel_name} {device_name}"

    cam_stats = get_camera_stats(camera_id)
    with stats_lock:
        stats["global"]["pictures_analyzed"] += 1
        cam_stats["pictures_analyzed"] += 1

    classes_csv = resolve_target_classes(camera_id, device_name, config)
    TARGET_CLASSES = _class_names_to_ids(model, classes_csv)

    detected_objects, detections_list, person_detected, max_confidence = _detect_image(model, TARGET_CLASSES, img_path)

    recognized_names = _recognize_faces(config, img_path) if person_detected else []
    _apply_face_names(detections_list, recognized_names)

    is_match = bool(detected_objects and max_confidence >= config.min_confidence)
    gemini_desc, caption = "", ""
    if is_match:
        with stats_lock:
            stats["global"]["matches_found"] += 1
            cam_stats["matches_found"] += 1
        gemini_desc, caption = _build_match_caption(config, img_path, detected_objects, location_context, recognized_names)
    else:
        print("No relevant targets detected or confidence below threshold.", file=sys.stderr)

    notified = _maybe_notify(
        config, camera_id, device_name, "picture", chat_id, force_chat_id, silent, is_match, cam_stats,
        lambda effective_chat_id: send_telegram_alert(config, img_path, caption, effective_chat_id)
    )

    image_filename = os.path.basename(img_path)
    _persist_detection(
        camera_id, device_name, channel_name, location_context, img_path, image_filename,
        detections_list, gemini_desc, is_match, notified
    )

    return {"description": gemini_desc, "objects": detections_list}

import os
import re

SENSITIVE_SETTINGS_KEYS = ("password", "key", "token", "secret")


def sanitize_component(value):
    safe = re.sub(r'[^A-Za-z0-9_.-]+', '_', value or '')
    return safe.strip('_')[:120]


def mask_settings(config):
    """Redacts secret-bearing config values before they leave the server."""
    masked = {}
    for key, value in vars(config).items():
        if not value:
            masked[key] = "Not Configured"
        elif any(s in key.lower() for s in SENSITIVE_SETTINGS_KEYS):
            str_val = str(value)
            masked[key] = f"••••{str_val[-4:]}" if len(str_val) > 8 else "••••••••"
        else:
            masked[key] = value
    return masked


def get_camera_folder(config, camera_id):
    return os.path.join(config.download_folder, sanitize_component(camera_id))

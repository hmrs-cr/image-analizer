import os
import re

SENSITIVE_SETTINGS_KEYS = ("password", "key", "token", "secret")
# Keys whose value embeds multiple raw credentials (not a single secret string), so they get
# fully redacted rather than partially revealed via the last-4-chars behavior below.
FULLY_SENSITIVE_KEYS = ("ftp_users",)


def sanitize_component(value):
    safe = re.sub(r'[^A-Za-z0-9_.-]+', '_', value or '')
    return safe.strip('_')[:120]


def mask_settings(config):
    """Redacts secret-bearing config values before they leave the server."""
    masked = {}
    for key, value in vars(config).items():
        if not value:
            masked[key] = "Not Configured"
        elif key in FULLY_SENSITIVE_KEYS:
            masked[key] = "Configured"
        elif any(s in key.lower() for s in SENSITIVE_SETTINGS_KEYS):
            str_val = str(value)
            masked[key] = f"••••{str_val[-4:]}" if len(str_val) > 8 else "••••••••"
        else:
            masked[key] = value
    return masked


def get_camera_folder(config, camera_id):
    return os.path.join(config.download_folder, sanitize_component(camera_id))

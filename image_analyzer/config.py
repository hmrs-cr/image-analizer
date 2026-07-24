import argparse
import os


def get_config():
    """Parses command line arguments with fallbacks to environment variables."""
    parser = argparse.ArgumentParser(
        description="Unified DVR Monitor: IMAP Downloader, Local AI Analyzer, and Telegram Notifier"
    )

    # IMAP Configuration
    parser.add_argument("--imap-server", default=os.environ.get("IMAP_SERVER", "imap.gmail.com"), help="IMAP server address")
    parser.add_argument("--email", default=os.environ.get("EMAIL_ACCOUNT"), help="Email account address")
    parser.add_argument("--password", default=os.environ.get("EMAIL_PASSWORD"), help="Gmail App Password")
    parser.add_argument("--from-address", default=os.environ.get("TARGET_FROM", ""), help="Optional: only process emails sent from this address")
    parser.add_argument("--subject", default=os.environ.get("TARGET_SUBJECT", ""), help="Optional: only process emails containing this subject")
    parser.add_argument("--imap-folder", default=os.environ.get("IMAP_FOLDER", "INBOX"), help="IMAP folder to monitor")
    parser.add_argument("--download-folder", default=os.environ.get("DOWNLOAD_FOLDER", "./cam_attachments"), help="Directory to save downloaded images")

    # FTP Configuration
    parser.add_argument("--ftp-host", default=os.environ.get("FTP_HOST", "0.0.0.0"), help="Host for the FTP image upload server")
    parser.add_argument("--ftp-port", type=int, default=int(os.environ.get("FTP_PORT", "2121")), help="Port for the FTP image upload server")
    parser.add_argument("--ftp-users", default=os.environ.get("FTP_USERS", ""), help="Allowed FTP users as 'user:pass;user2:pass2'; each username must be in 'device-channel' format")

    # AI Configuration
    parser.add_argument("--model-name", default=os.environ.get("YOLO_MODEL", "yolov8n.pt"), help="YOLO model version")
    parser.add_argument("--facial-db", default=os.environ.get("FACIAL_DB_PATH", ""), help="Directory path for DeepFace known identities")
    parser.add_argument("--min-confidence", type=float, default=float(os.environ.get("MIN_CONFIDENCE", "0.0")), help="Minimum confidence threshold (0.0-1.0)")
    parser.add_argument("--classes", default=os.environ.get("TARGET_CLASSES", "person,bicycle,car,motorcycle,bus,truck,bird,cat,dog,horse,sheep,cow"), help="Comma-separated COCO class names to detect")

    # Gemini Configuration
    parser.add_argument("--gemini-api-key", default=os.environ.get("GEMINI_API_KEY"), help="Google Gemini API Key")
    parser.add_argument("--gemini-model", default=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"), help="Google Gemini model version")
    parser.add_argument("--gemini-system-prompt-file", default=os.environ.get("GEMINI_SYSTEM_PROMPT_FILE", ""), help="Path to a txt file containing the Gemini system prompt")
    parser.add_argument("--gemini-timeout", type=float, default=float(os.environ.get("GEMINI_TIMEOUT", "30.0")), help="Timeout for Gemini requests in seconds")

    # Telegram Configuration
    parser.add_argument("--telegram-token", default=os.environ.get("TELEGRAM_TOKEN"), help="Telegram Bot API Token")
    parser.add_argument("--telegram-chat-id", default=os.environ.get("TELEGRAM_CHAT_ID"), help="Telegram Chat ID to send alerts to")
    parser.add_argument("--telegram-retries", type=int, default=int(os.environ.get("TELEGRAM_RETRIES", "3")), help="Number of retries to send the notification if it fails")

    # HTTP Server Configuration
    parser.add_argument("--host", default=os.environ.get("HTTP_HOST", "0.0.0.0"), help="Host for the HTTP API server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("HTTP_PORT", "5000")), help="Port for the HTTP API server")

    # Access Control
    parser.add_argument("--auth-username", default=os.environ.get("AUTH_USERNAME"), help="Username for HTTP Basic Auth protecting the dashboard and API (all routes except /analyze-image)")
    parser.add_argument("--auth-password", default=os.environ.get("AUTH_PASSWORD"), help="Password for HTTP Basic Auth protecting the dashboard and API")
    parser.add_argument("--analyze-shared-secret", default=os.environ.get("ANALYZE_SHARED_SECRET"), help="Shared secret required from automated callers of /analyze-image, sent via the X-Analyze-Secret header")

    args = parser.parse_args()
    return args

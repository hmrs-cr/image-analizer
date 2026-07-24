import sys
import threading
from abc import ABC, abstractmethod


class ImageSource(ABC):
    """Base class for background image sources (IMAP inbox, FTP watcher, Dropbox, ...).

    Subclasses implement run() -- a blocking loop that watches for new images and calls
    self.on_image(filepath, device_name, channel_name, chat_id) for each one found. The
    callback runs the shared analysis pipeline; sources never need to know about it.
    """

    def __init__(self, config, on_image):
        self.config = config
        self.on_image = on_image

    @classmethod
    def is_configured(cls, config) -> bool:
        """Whether this source has what it needs to run. False skips it at startup."""
        return True

    @abstractmethod
    def run(self):
        ...

    def start(self):
        """Runs run() in a daemon thread and returns the Thread."""
        thread = threading.Thread(target=self._run_safe, daemon=True, name=type(self).__name__)
        thread.start()
        return thread

    def _run_safe(self):
        try:
            self.run()
        except Exception as e:
            print(f"{type(self).__name__} crashed: {e}", file=sys.stderr)

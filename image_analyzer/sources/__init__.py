from .base import ImageSource
from .imap_source import ImapEmailSource

# Registry of available background image sources. Add a new source (FTP, Dropbox, ...) by
# implementing ImageSource in its own module and appending its class here.
AVAILABLE_SOURCES = [ImapEmailSource]

__all__ = ["ImageSource", "ImapEmailSource", "AVAILABLE_SOURCES"]

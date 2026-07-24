from .base import ImageSource
from .ftp_source import FtpImageSource
from .imap_source import ImapEmailSource

# Registry of available background image sources. Add a new source (Dropbox, ...) by
# implementing ImageSource in its own module and appending its class here.
AVAILABLE_SOURCES = [ImapEmailSource, FtpImageSource]

__all__ = ["ImageSource", "ImapEmailSource", "FtpImageSource", "AVAILABLE_SOURCES"]

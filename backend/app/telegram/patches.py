"""Changes made to Telethon from the outside, applied once at startup.

Telethon is not patched lightly: every one of these has to be a measured problem with no
way through the public API, and it has to be described here well enough that the next
upgrade can tell whether it is still needed.
"""

from __future__ import annotations

import logging

from telethon.tl.core import GzipPacked

log = logging.getLogger(__name__)

# Above this, a request body is a media part and nothing else. A caption is 1024
# characters, a message 4096: everything the protocol carries that could ever compress
# stays well underneath, so the original behaviour is kept there.
GZIP_SKIP_ABOVE = 16 * 1024


def _no_gzip_for_media(original):
    """Stops Telethon from gzipping the parts of an upload.

    `MTProtoState.write_data_as_message` runs every outgoing request through
    `gzip_if_smaller`, which compresses anything over 512 bytes at level 9 and keeps the
    result only if it came out shorter. On a 512KB part of a video, which is what this
    application spends its bandwidth on, the answer is always no: measured on the image
    this runs in, gzip level 9 does 37.7 MB/s on one core and returns 178 bytes MORE than
    it was given, so the compressed copy is built and thrown away for every part.

    That is the whole ceiling. The process is single threaded, one asyncio loop, and at
    around 20 MB/s of upload it sat at 100% of one core: adding a second account changed
    nothing because the limit was never Telegram's 20 connections per data center, it was
    this. For comparison, on the same core AES-IGE through cryptg runs at 298 MB/s and
    SHA-256 at 1600 MB/s, so once the gzip is gone the Python side is no longer what
    decides the speed.

    Skipping compression is always accepted by the server: a gzipped body is an option
    the client may take, never an obligation.
    """

    def gzip_if_smaller(content_related, data):
        if len(data) > GZIP_SKIP_ABOVE:
            return data
        return original(content_related, data)

    return gzip_if_smaller


def apply() -> None:
    GzipPacked.gzip_if_smaller = staticmethod(
        _no_gzip_for_media(GzipPacked.gzip_if_smaller)
    )
    log.info("Telethon patched: no gzip on request bodies above %d bytes", GZIP_SKIP_ABOVE)

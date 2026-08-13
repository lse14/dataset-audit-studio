from __future__ import annotations

import os
import threading
from collections import OrderedDict
from pathlib import Path

from dataset_audit_studio.export.image_conversion import (
    ImageExportFormat,
    encode_export_image,
)

# Keep process-local encoded bytes bounded independently of dataset size.
DEFAULT_TRANSCODE_CACHE_MAX_BYTES = 64 * 1024 * 1024

TranscodeCacheKey = tuple[str, int, int, str]


def transcode_cache_key(source: Path, image_format: ImageExportFormat) -> TranscodeCacheKey:
    resolved = source.resolve(strict=False)
    stat = resolved.stat()
    return (
        os.path.normcase(str(resolved)),
        int(stat.st_mtime_ns),
        int(stat.st_size),
        image_format,
    )


class TranscodeCache:
    """Process-local LRU for encoded export bytes. Misses only cost another encode."""

    def __init__(self, max_bytes: int = DEFAULT_TRANSCODE_CACHE_MAX_BYTES) -> None:
        self._max_bytes = max(0, max_bytes)
        self._entries: OrderedDict[TranscodeCacheKey, bytes] = OrderedDict()
        self._nbytes = 0
        self._lock = threading.Lock()

    def get(self, key: TranscodeCacheKey) -> bytes | None:
        with self._lock:
            encoded = self._entries.get(key)
            if encoded is not None:
                self._entries.move_to_end(key)
            return encoded

    def put(self, key: TranscodeCacheKey, encoded: bytes) -> None:
        size = len(encoded)
        with self._lock:
            if size > self._max_bytes:
                previous = self._entries.pop(key, None)
                if previous is not None:
                    self._nbytes -= len(previous)
                return
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._nbytes -= len(previous)
            self._entries[key] = encoded
            self._nbytes += size
            while self._nbytes > self._max_bytes and self._entries:
                _, evicted = self._entries.popitem(last=False)
                self._nbytes -= len(evicted)

    def encode(self, source: Path, image_format: ImageExportFormat) -> bytes:
        key = transcode_cache_key(source, image_format)
        hit = self.get(key)
        if hit is not None:
            return hit
        encoded = encode_export_image(source, image_format)
        self.put(key, encoded)
        return encoded


_PROCESS_CACHE = TranscodeCache()


def cached_encode_export_image(source: Path, image_format: ImageExportFormat) -> bytes:
    return _PROCESS_CACHE.encode(source, image_format)

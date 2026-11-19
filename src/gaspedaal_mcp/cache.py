"""Schijfcache voor geparste pagina's (patroon van carbase-mcp).

Atomisch schrijven, bestanden 0600 in een eigen map 0700, eviction op totale
grootte (MB) en leeftijd (dagen). Een map die niet van deze gebruiker is of door
anderen beschrijfbaar is, wordt niet gebruikt: de cache staat dan uit.
Bewaard wordt het geparste JSON-resultaat, niet de ruwe HTML. De TTL per soort
bepaalt de aanroeper bij het lezen.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import stat
import tempfile
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def _default_dir() -> Path:
    if raw := os.environ.get("GASPEDAAL_MCP_CACHE_DIR"):
        return Path(raw)
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "gaspedaal-mcp"


class DiskCache:
    """Sleutel → JSON-dict op schijf. ``max_mb == 0`` zet de cache uit."""

    def __init__(self, directory: Path | None = None, max_mb: int | None = None, max_days: int | None = None):
        self.directory = directory or _default_dir()
        self.max_bytes = (max_mb if max_mb is not None else _env_int("GASPEDAAL_MCP_CACHE_MB", 200)) << 20
        self.max_age = (max_days if max_days is not None else _env_int("GASPEDAAL_MCP_CACHE_DAYS", 7)) * 86400
        self.enabled = self.max_bytes > 0
        self._safe: bool | None = None

    def _dir_ok(self) -> bool:
        """Alleen een echte map van deze gebruiker waar niemand anders in kan schrijven.

        In een gedeelde plek (bv. onder /tmp) kan een ander anders entries of symlinks
        neerzetten: valse data voor het taalmodel, of een bestand van ons dat overschreven wordt.
        """
        if self._safe is None:
            try:
                self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                st = os.lstat(self.directory)
                mine = not hasattr(os, "getuid") or st.st_uid == os.getuid()
                self._safe = stat.S_ISDIR(st.st_mode) and mine and not st.st_mode & 0o022
            except OSError:
                self._safe = False
            if not self._safe:
                log.warning("Cache disabled: %s is not a private directory of this user", self.directory)
        return self._safe

    def _path(self, key: str) -> Path:
        return self.directory / f"{hashlib.sha256(key.encode()).hexdigest()}.json"

    def get(self, key: str, ttl_seconds: int) -> dict[str, Any] | None:
        """De waarde, of ``None`` als die ontbreekt, onleesbaar is of ouder dan ``ttl_seconds``."""
        if not self.enabled or not self._dir_ok():
            return None
        path = self._path(key)
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except OSError:
            return None  # ontbreekt (of onleesbaar)
        except ValueError:
            entry = {}  # corrupt: hieronder weggooien
        entry = entry if isinstance(entry, dict) else {}
        value, cached_at = entry.get("value"), entry.get("_cached_at")
        if (
            not isinstance(value, dict)
            or not isinstance(cached_at, (int, float))
            or time.time() - cached_at > min(ttl_seconds, self.max_age)
        ):
            with contextlib.suppress(OSError):
                path.unlink()  # verlopen of onbruikbaar
            return None
        return value

    def set(self, key: str, value: dict[str, Any]) -> None:
        """Atomisch schrijven; fouten kosten alleen de cache, nooit de toolaanroep."""
        if not self.enabled or not self._dir_ok():
            return
        path = self._path(key)
        tmp = None
        try:
            # mkstemp: nieuw bestand met een willekeurige naam (O_EXCL, 0600), volgt geen symlink.
            fd, tmp = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"_cached_at": time.time(), "value": value}, fh, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError:
            if tmp:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
            return
        self._evict(keep=path)

    def _evict(self, keep: Path) -> None:
        """Eerst te oude entries weg, dan de oudste tot de cache binnen ``max_bytes`` past."""
        entries = []
        for p in self.directory.glob("*.json"):
            with contextlib.suppress(OSError):
                st = p.stat()
                entries.append((st.st_mtime, st.st_size, p))
        entries.sort()
        total = sum(size for _, size, _ in entries)
        cutoff = time.time() - self.max_age
        for mtime, size, p in entries:
            if p != keep and (mtime < cutoff or total > self.max_bytes):
                with contextlib.suppress(OSError):
                    p.unlink()
                    total -= size

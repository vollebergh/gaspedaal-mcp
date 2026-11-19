"""Schijfcache voor opgehaalde (geparste) pagina's.

Gebaseerd op het patroon van carbase-mcp: atomisch schrijven, bestand 0600,
eigen map 0700, evictie op totale grootte (MB) en op leeftijd (dagen).
Bewaard wordt niet de ruwe HTML maar het geparste JSON-resultaat.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

_ENV = {
    "dir": "GASPEDAAL_MCP_CACHE_DIR",
    "mb": "GASPEDAAL_MCP_CACHE_MB",
    "days": "GASPEDAAL_MCP_CACHE_DAYS",
}

_DEFAULT_PATH = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "gaspedaal-mcp"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        # Ongeldige env-var: val terug op de standaard in plaats van te crashen.
        return default


def _cache_dir() -> Path:
    raw = os.environ.get(_ENV["dir"])
    return Path(raw) if raw else _DEFAULT_PATH


def _cache_mb() -> int:
    return _env_int(_ENV["mb"], 200)


def _cache_days() -> int:
    return _env_int(_ENV["days"], 7)


class DiskCache:
    """Key → JSON-value op schijf, met grootte- en leeftijdslimiet.

    De sleutel is de URL; de waarde is een dict dat als JSON wordt opgeslagen.
    ``max_mb == 0`` zet de cache uit (get/set zijn dan no-ops).
    """

    def __init__(
        self,
        directory: Path | None = None,
        max_mb: int | None = None,
        max_days: int | None = None,
    ) -> None:
        self.directory = directory if directory is not None else _cache_dir()
        self.max_mb = max_mb if max_mb is not None else _cache_mb()
        self.max_days = max_days if max_days is not None else _cache_days()
        self.enabled = self.max_mb > 0

    def _path(self, key: str) -> Path:
        name = hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json"
        return self.directory / name

    def _read_entry(self, path: Path) -> dict[str, Any] | None:
        try:
            with path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def _evict(self, keep: Path | None = None) -> None:
        files = [p for p in self.directory.glob("*.json") if p.is_file()]
        files.sort(key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        limit = self.max_mb * 1024 * 1024
        cutoff = time.time() - self.max_days * 86400
        for p in files:
            if p == keep:
                continue
            if total > limit or p.stat().st_mtime < cutoff:
                total -= p.stat().st_size
                with contextlib.suppress(OSError):
                    p.unlink()

    def get(self, key: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        path = self._path(key)
        if not path.is_file():
            return None
        if path.stat().st_mtime < time.time() - self.max_days * 86400:
            with contextlib.suppress(OSError):
                path.unlink()
            return None
        return self._read_entry(path)

    def set(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        if not self.enabled:
            return
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self._path(key)
        payload = {"_cached_at": time.time(), "_ttl": ttl_seconds, "value": value}
        tmp = path.with_suffix(".json.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.chmod(tmp, 0o600)
            tmp.replace(path)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
        self._evict(keep=path)

    def get_with_ttl(self, key: str, ttl_seconds: int) -> dict[str, Any] | None:
        """Zoals ``get``, maar beschouwt de entry als verlopen na ``ttl_seconds``.

        De TTL die bij ``set`` is meegegeven zit al in de entry; deze methode
        gebruikt echter de *opgegeven* TTL (de TTL-soort hangt af van de call),
        net zoals de server die per verzoek bepaalt.
        """
        if not self.enabled:
            return None
        path = self._path(key)
        if not path.is_file():
            return None
        entry = self._read_entry(path)
        if entry is None:
            return None
        cached_at = entry.get("_cached_at", 0)
        if time.time() - cached_at > ttl_seconds:
            with contextlib.suppress(OSError):
                path.unlink()
            return None
        return entry.get("value")

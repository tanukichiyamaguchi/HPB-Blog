"""Common helpers: logging, JST, paths, JSON I/O, retry."""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, TypeVar

from src.config import JST, OUTPUT_DIR

T = TypeVar("T")

_LOG_CONFIGURED = False

_LOG_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def setup_logging(level: int | None = None) -> logging.Logger:
    """Initialize root logging once.

    Level resolution: explicit ``level`` arg > ``LOG_LEVEL`` env > INFO.
    """
    global _LOG_CONFIGURED
    if not _LOG_CONFIGURED:
        if level is None:
            env_level = os.environ.get("LOG_LEVEL", "").strip().upper()
            level = _LOG_LEVEL_MAP.get(env_level, logging.INFO)
        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
        _LOG_CONFIGURED = True
    return logging.getLogger("hpb-blog")


def get_jst_now() -> datetime:
    return datetime.now(JST)


def get_today_date_str(now: datetime | None = None) -> str:
    return (now or get_jst_now()).strftime("%Y-%m-%d")


def get_today_output_dir(now: datetime | None = None) -> Path:
    p = OUTPUT_DIR / get_today_date_str(now)
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_season(month: int) -> str:
    if not 1 <= month <= 12:
        raise ValueError(f"month must be 1-12, got {month}")
    if 3 <= month <= 5:
        return "春"
    if 6 <= month <= 8:
        return "夏"
    if 9 <= month <= 11:
        return "秋"
    return "冬"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    """Atomically write JSON: write to tmp then rename.

    Prevents a partially-written file from corrupting state if the process is
    killed mid-write (e.g., theme_history.json during a CI run).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_path_str = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp_path_str, str(path))  # atomic on POSIX and Windows
    except Exception:
        # Best-effort cleanup of the temp file on failure
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise


def retry(
    fn: Callable[[], T],
    *,
    max_attempts: int = 3,
    base_delay: float = 2.0,
    logger: logging.Logger | None = None,
) -> T:
    """Run fn() with exponential backoff. Returns the result on success."""
    log = logger or logging.getLogger(__name__)
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - we re-raise after retries
            last_exc = e
            if attempt == max_attempts:
                log.error("Final attempt %d/%d failed: %s", attempt, max_attempts, e)
                raise
            delay = base_delay * (2 ** (attempt - 1))
            log.warning(
                "Attempt %d/%d failed: %s. Retrying in %.1fs",
                attempt, max_attempts, e, delay,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc



def retry(
    fn: Callable[[], T],
    *,
    max_attempts: int = 3,
    base_delay: float = 2.0,
    logger: logging.Logger | None = None,
) -> T:
    """Run fn() with exponential backoff. Returns the result on success."""
    log = logger or logging.getLogger(__name__)
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - we re-raise after retries
            last_exc = e
            if attempt == max_attempts:
                log.error("Final attempt %d/%d failed: %s", attempt, max_attempts, e)
                raise
            delay = base_delay * (2 ** (attempt - 1))
            log.warning(
                "Attempt %d/%d failed: %s. Retrying in %.1fs",
                attempt, max_attempts, e, delay,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc

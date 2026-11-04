"""Shared helpers: ports, user data paths, logging and child-process plumbing."""

import json
import multiprocessing as mp
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

from loguru import logger

APP_DIR_NAME = "GhostCoder"
LOG_FORMAT = "[{time:YYYY-MM-DD HH:mm:ss.SSS}] {level} {process.name} {name}:{function}:{line} - {message}"
MAX_LOG_BYTES = 10 * 1024 * 1024


def get_random_available_port() -> int:
    """Ask the OS for a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_for_port(host: str, port: int, timeout: float = 10.0, alive=lambda: True) -> bool:
    """Block until a TCP server accepts connections on host:port (or timeout / alive() is False)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and alive():
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _env_dir(var: str, fallback: Path) -> Path:
    base = os.environ.get(var)
    return Path(base) if base else fallback


def config_dir() -> Path:
    """Per-user settings directory (%APPDATA%\\GhostCoder)."""
    path = _env_dir("APPDATA", Path.home() / "AppData" / "Roaming") / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir() -> Path:
    """Per-user log directory (%LOCALAPPDATA%\\GhostCoder\\logs)."""
    path = _env_dir("LOCALAPPDATA", Path.home() / "AppData" / "Local") / APP_DIR_NAME / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_file() -> Path:
    return log_dir() / "ghost_coder.log"


def rotate_log_if_large() -> None:
    """Called once by the main process before children start (loguru rotation is not multi-process safe)."""
    path = log_file()
    try:
        if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
            backup = path.with_suffix(".log.1")
            backup.unlink(missing_ok=True)
            path.rename(backup)
    except OSError:
        pass


def setup_logging(enabled: bool) -> None:
    """Configure loguru identically in every process."""
    if not enabled:
        logger.remove()
        logger.disable("ghost_coder")
        return
    handlers: list[dict[str, Any]] = [
        {"sink": str(log_file()), "format": LOG_FORMAT, "level": "DEBUG", "encoding": "utf-8"},
    ]
    # Frozen GUI builds have no console, so sys.stderr can be None.
    if sys.stderr is not None:
        handlers.append({"sink": sys.stderr, "format": LOG_FORMAT, "level": "DEBUG"})
    logger.configure(handlers=handlers)
    logger.enable("ghost_coder")


def read_json(path: Path, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as e:
        logger.warning(f"Could not read {path}: {e}")
        return default


def write_json(path: Path, data: Any) -> None:
    """Write JSON atomically so a crash never leaves a truncated settings file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def unique_client_id(role: str) -> str:
    """MQTT client ids must be unique per broker (matters when an external broker is shared)."""
    return f"ghost-coder-{role}-{os.getpid()}"


def start_parent_watchdog(on_parent_exit, interval: float = 1.0) -> None:
    """Call on_parent_exit() if the process that spawned us dies (prevents orphaned children)."""
    parent = mp.parent_process()
    if parent is None:
        return

    def watch():
        while parent.is_alive():
            time.sleep(interval)
        logger.warning("Parent process exited; shutting down")
        on_parent_exit()

    threading.Thread(target=watch, name="parent-watchdog", daemon=True).start()

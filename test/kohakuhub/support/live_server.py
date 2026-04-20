"""Live HTTP server helpers for compatibility-focused backend tests."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import socket
import threading
import time

import requests
import uvicorn


@dataclass(slots=True)
class LiveServerHandle:
    """Handle for a background uvicorn process used in tests."""

    base_url: str
    server: uvicorn.Server
    thread: threading.Thread


def _reserve_local_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_live_server(app, timeout_seconds: float = 30.0) -> LiveServerHandle:
    """Start the FastAPI app on a local TCP port for real HTTP client tests."""
    port = _reserve_local_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="on",
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not thread.is_alive():
            raise RuntimeError("Live backend test server exited before becoming healthy.")
        try:
            response = requests.get(f"{base_url}/health", timeout=1.0)
            if response.status_code == 200:
                return LiveServerHandle(
                    base_url=base_url,
                    server=server,
                    thread=thread,
                )
        except Exception:
            pass
        time.sleep(0.1)

    server.should_exit = True
    thread.join(timeout=5.0)
    raise TimeoutError("Timed out waiting for the live backend test server.")


def stop_live_server(handle: LiveServerHandle, timeout_seconds: float = 10.0) -> None:
    """Stop a background uvicorn server started for tests."""
    handle.server.should_exit = True
    handle.thread.join(timeout=timeout_seconds)
    if handle.thread.is_alive():
        raise TimeoutError("Timed out stopping the live backend test server.")

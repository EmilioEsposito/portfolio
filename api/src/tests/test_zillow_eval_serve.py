"""The eval pages are served to this machine only (no network beyond loopback)."""

import threading
import urllib.request
from pathlib import Path

from api.src.sernia_ai.zillow_leads.evals.cli import page_server


def test_pages_are_served_on_loopback_and_never_cached(tmp_path: Path):
    (tmp_path / "review.html").write_text("<p>review</p>")
    server = page_server(tmp_path, 0)  # port 0: any free port
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/review.html", timeout=5) as reply:
            assert reply.read() == b"<p>review</p>"
            assert reply.headers["Cache-Control"] == "no-store"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert host == "127.0.0.1"

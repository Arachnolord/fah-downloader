"""Beweist die Ursache des Abbruch-Bugs gegen echtes yt-dlp.

Aufbau: lokaler HTTP-Server liefert eine grosse Datei langsam aus. yt-dlp laedt
sie mit ignoreerrors (wie in der App). Der Progress-Hook bricht nach den ersten
Bytes ab -- einmal mit einer eigenen Exception (alter Code), einmal mit
DownloadCancelled (neuer Code).
"""
import http.server
import socketserver
import tempfile
import threading
import time
from pathlib import Path

import yt_dlp
from yt_dlp.utils import DownloadCancelled

SIZE = 40 * 1024 * 1024


class Handler(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(SIZE))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(SIZE))
        self.end_headers()
        chunk = b"\0" * 65536
        sent = 0
        try:
            while sent < SIZE:
                self.wfile.write(chunk)
                sent += len(chunk)
                time.sleep(0.004)          # gedrosselt, damit der Abbruch greift
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *a):
        pass


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def run(exc_factory, label):
    """Laedt an und bricht im Hook ab. Gibt zurueck, was extract_info tut."""
    hits = {"n": 0}

    def hook(d):
        if d.get("status") == "downloading" and d.get("downloaded_bytes", 0) > 512 * 1024:
            hits["n"] += 1
            raise exc_factory()

    with tempfile.TemporaryDirectory() as td:
        opts = {
            "quiet": True, "no_warnings": True, "noprogress": True,
            "ignoreerrors": "only_download",
            "outtmpl": str(Path(td) / "%(title)s.%(ext)s"),
            "progress_hooks": [hook],
            "continuedl": True,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(URL, download=True)
            return f"extract_info kehrte ZURUECK mit info={'dict' if info else 'None'}"
        except DownloadCancelled:
            return "DownloadCancelled ist DURCHGEKOMMEN"
        except Exception as e:
            return f"andere Exception: {type(e).__name__}"


class Eigene(Exception):
    """So sah der Abbruch vor dem Fix aus."""


with _Server(("127.0.0.1", 0), Handler) as srv:
    port = srv.server_address[1]
    URL = f"http://127.0.0.1:{port}/clip.mp4"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.2)

    print("ALT  (eigene Exception-Klasse):")
    print("     ->", run(Eigene, "alt"))
    print("NEU  (yt_dlp.utils.DownloadCancelled):")
    print("     ->", run(DownloadCancelled, "neu"))
    srv.shutdown()

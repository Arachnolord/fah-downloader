# ============================================================================
# Erdacht und programmiert von Fabian Hammer.
# (c) 2026 Fabian Hammer - Alle Rechte vorbehalten. Geistiges Eigentum.
# ============================================================================
"""FastAPI-Server: WebGUI + REST-API + WebSocket-Live-Fortschritt."""
import asyncio
import functools
import mimetypes
import os
import re
import time
from urllib.parse import quote
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse, Response, StreamingResponse)
from fastapi.staticfiles import StaticFiles

import auth
import downloader
import logins
import playback
import shares
from config import (COOKIES_FILE, DOWNLOAD_ROOTS, DOWNLOADS_DIR, HOSTFS_DIR,
                    HOSTFS_HOST_PATH, PLAYLIST_MODES, SHARES_DIR,
                    VIDEO_CONTAINERS, get_settings, save_settings)
from version import VERSION

STATIC = Path(__file__).parent / "static"

_loop = None

# Container, die Browser nicht kennen -- ohne korrekten Typ liefert der Player
# gar nichts, statt wenigstens den Download anzubieten.
_EXTRA_MIME = {
    ".mkv": "video/x-matroska", ".webm": "video/webm", ".m4v": "video/mp4",
    ".mov": "video/quicktime", ".flv": "video/x-flv", ".avi": "video/x-msvideo",
    ".m4a": "audio/mp4", ".opus": "audio/ogg", ".flac": "audio/flac",
    ".ogg": "audio/ogg", ".oga": "audio/ogg", ".wav": "audio/wav",
}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _loop
    _loop = asyncio.get_running_loop()
    auth.bootstrap_from_env()
    # Migrierte Einstellungen einmal zurueckschreiben. get_settings() migriert
    # ohnehin bei jedem Lesen, aber ohne diesen Schritt bliebe in der
    # settings.json auf der Platte der alte, wirkungslose Wert stehen (z.B.
    # custom_format="mkv") -- verwirrend fuer jeden, der die Datei ansieht.
    save_settings({})
    shares.remount_all()
    downloader.start()
    yield


app = FastAPI(title="FAH Media Downloader", version=VERSION, lifespan=lifespan)


# ── Auth-Helfer ─────────────────────────────────────────────────────────────
def _current_user(request: Request):
    return auth.read_session(request.cookies.get("session", ""))


def _require(request: Request):
    return _current_user(request) is not None


def _set_session(resp, request: Request, username: str):
    resp.set_cookie("session", auth.make_session(username), httponly=True,
                    samesite="lax", max_age=auth.SESSION_MAX_AGE,
                    # Hinter einem HTTPS-Reverse-Proxy soll das Cookie das
                    # Netz nie im Klartext verlassen.
                    secure=request.url.scheme == "https")
    return resp


PUBLIC_PATHS = {"/login", "/api/login", "/api/setup", "/api/needs-setup",
                "/health", "/favicon.ico", "/favicon.svg",
                "/apple-touch-icon.png", "/site.webmanifest"}


@app.middleware("http")
async def _auth_mw(request: Request, call_next):
    path = request.url.path
    if (path in PUBLIC_PATHS or path.startswith("/static/")
            or path.startswith("/assets/")):
        return await call_next(request)
    if not _require(request):
        if path.startswith("/api/"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return RedirectResponse("/login")
    return await call_next(request)


# ── Pfad-Absicherung ────────────────────────────────────────────────────────
# None = noch nie gebaut. Kein 0.0-Startwert: direkt nach einem Neustart ist
# time.monotonic() klein, und die leere Startbelegung haette als "noch gueltig"
# gegolten -- dann waere jeder Pfad als unerlaubt abgewiesen worden.
_roots_cache = None
_ROOTS_TTL = 5.0


def _roots():
    """Erlaubte Wurzeln -- kurz gecacht.

    Wird pro Pfadpruefung aufgerufen, also auch bei jeder einzelnen
    Range-Anfrage des Players. Ohne Cache waere das jedes Mal ein
    Verzeichnis-Listing von SHARES_DIR.
    """
    global _roots_cache
    now = time.monotonic()
    if _roots_cache is not None and now - _roots_cache[0] < _ROOTS_TTL:
        return _roots_cache[1]
    roots = _build_roots()
    _roots_cache = (now, roots)
    return roots


def _host_label(pfad: Path) -> str:
    """Anzeigename einer Host-Wurzel -- mit dem Host-Pfad, wenn er bekannt ist.

    Im Container heisst alles /hostfs/...; der Nutzer kennt aber nur den
    Pfad auf seinem NAS (z.B. /volume1/medien). HOSTFS_HOST_PATH kommt aus
    der docker-compose und macht die Anzeige verstaendlich.
    """
    rel = pfad.relative_to(HOSTFS_DIR).as_posix() if pfad != HOSTFS_DIR else ""
    if HOSTFS_HOST_PATH:
        host = HOSTFS_HOST_PATH.rstrip("/") + ("/" + rel if rel else "")
        return f"Host: {host or '/'}"
    return f"Host: /{rel}" if rel else "Host-Dateisystem"


def _build_roots():
    roots = [("Downloads", str(DOWNLOADS_DIR))]
    if DOWNLOAD_ROOTS:
        # Einschraenkung per DOWNLOAD_ROOTS: nur diese Teilbaeume sind Ziel.
        # Fehlt einer davon auf der Platte, wird er stillschweigend
        # ausgelassen -- er wuerde ohnehin bei jeder Pfadpruefung scheitern.
        for w in DOWNLOAD_ROOTS:
            if w.is_dir():
                roots.append((_host_label(w), str(w)))
    elif HOSTFS_DIR.exists():
        roots.append((_host_label(HOSTFS_DIR), str(HOSTFS_DIR)))
    if SHARES_DIR.exists():
        for d in sorted(SHARES_DIR.iterdir()):
            if d.is_dir():
                roots.append((f"NAS: {d.name}", str(d)))
    return roots


def _roots_hinweis() -> str:
    """Satz fuer die Oberflaeche: welcher Bereich des Hosts ist erlaubt?"""
    if DOWNLOAD_ROOTS:
        namen = [_host_label(w).replace("Host: ", "") for w in DOWNLOAD_ROOTS
                 if w.is_dir()]
        fehlend = [str(w) for w in DOWNLOAD_ROOTS if not w.is_dir()]
        text = ("Erlaubte Bereiche auf dem Host: " + ", ".join(namen)
                if namen else "Kein erlaubter Host-Bereich vorhanden")
        text += " (DOWNLOAD_ROOTS in der .env)."
        if fehlend:
            text += " Nicht gefunden: " + ", ".join(fehlend) + "."
        return text
    if HOSTFS_DIR.exists():
        wo = HOSTFS_HOST_PATH or "das eingehaengte Host-Dateisystem"
        return (f"Auf dem Host ist {wo} als Ziel waehlbar (HOSTFS_MOUNT); "
                "mit DOWNLOAD_ROOTS in der .env laesst sich das auf einzelne "
                "Ordner einschraenken.")
    return "Kein Host-Dateisystem eingehaengt -- nur Downloads und NAS-Freigaben."


def _allowed_path(raw: str):
    """Aufgeloesten Pfad zurueckgeben -- oder None, wenn er ausserhalb liegt.

    Wird von JEDEM Endpunkt genutzt, der einen Pfad entgegennimmt. Frueher
    pruefte nur der Ordner-Browser; Download-Ziel und Dateiauslieferung haetten
    sonst ueberallhin im Container schreiben bzw. lesen koennen.
    """
    if not raw:
        return None
    try:
        target = Path(raw).resolve()
    except (OSError, RuntimeError, ValueError):
        # ValueError kommt von einem NUL-Byte im Pfad -- ohne diesen Fall
        # antwortete der Endpunkt mit 500 statt mit einem sauberen 403.
        return None
    for _, root in _roots():
        r = Path(root).resolve()
        if target == r or str(target).startswith(str(r) + os.sep):
            return target
    return None


def _is_root(p: Path) -> bool:
    return any(p == Path(r).resolve() for _, r in _roots())


# ── Seiten ──────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"ok": True, "version": VERSION}


@app.get("/api/version")
async def api_version():
    return {"version": VERSION}


@functools.lru_cache(maxsize=1)
def _index_html() -> str:
    """index.html mit der Versionsnummer ausliefern.

    Die Versionsnummer haengt als ?v=... an app.js und style.css. Ohne diesen
    Cache-Buster serviert der Browser nach einem Update die alten Dateien --
    das Deploy sieht dann fehlerhaft aus, obwohl es sauber durchlief.
    """
    return (STATIC / "index.html").read_text(encoding="utf-8") \
        .replace("__VERSION__", VERSION)


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(_index_html(),
                        headers={"Cache-Control": "no-cache, must-revalidate"})


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return FileResponse(STATIC / "login.html")


# Icons an den Wurzelpfaden, wo Browser und Betriebssysteme sie erwarten.
@app.get("/favicon.ico")
async def favicon_ico():
    return FileResponse(STATIC / "favicon.ico", media_type="image/x-icon",
                        headers={"Cache-Control": "public, max-age=604800"})


@app.get("/favicon.svg")
async def favicon_svg():
    return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml",
                        headers={"Cache-Control": "public, max-age=604800"})


@app.get("/apple-touch-icon.png")
async def apple_icon():
    return FileResponse(STATIC / "apple-touch-icon.png", media_type="image/png",
                        headers={"Cache-Control": "public, max-age=604800"})


@app.get("/site.webmanifest")
async def webmanifest():
    return FileResponse(STATIC / "site.webmanifest",
                        media_type="application/manifest+json")


@app.get("/api/needs-setup")
async def needs_setup():
    return {"needs_setup": not auth.has_admin()}


@app.post("/api/setup")
async def setup(request: Request):
    if auth.has_admin():
        return JSONResponse({"error": "already-set"}, status_code=400)
    body = await request.json()
    try:
        # Pruefen und Anlegen passieren in auth.setup_admin unter EINEM Lock:
        # zwei gleichzeitige Erstaufrufe legten sonst zwei Admins an.
        auth.setup_admin(body.get("username", ""), body.get("password", ""))
    except auth.SchonEingerichtet:
        return JSONResponse({"error": "already-set"}, status_code=400)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return _set_session(JSONResponse({"ok": True}), request, body["username"])


@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    u, p = body.get("username", ""), body.get("password", "")
    if not auth.verify(u, p):
        return JSONResponse({"error": "Ungueltige Zugangsdaten."}, status_code=401)
    return _set_session(JSONResponse({"ok": True}), request, u)


@app.post("/api/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("session")
    return resp


# ── Downloads ───────────────────────────────────────────────────────────────
# Was ein Client je Download mitgeben darf -- bewusst eine Positivliste.
# Bei einer Ausschlussliste wandert jedes neue interne Feld ungefragt in den
# Job; "subdir" waere so als "../../etc" ein Ausbruch aus dem Zielordner.
_ERLAUBTE_OPTIONEN = frozenset((
    "dest", "format_mode", "video_container", "max_quality", "audio_format",
    "audio_quality", "custom_format", "output_template", "playlist_items",
    "playlist_mode",
))


def _overrides_from(body: dict) -> dict:
    """Aus dem Request-Body die Optionen fuer den Job herausloesen."""
    return {k: v for k, v in body.items() if k in _ERLAUBTE_OPTIONEN}


def _tmpl_ok(tmpl: str) -> bool:
    """Dateinamen-Muster duerfen Unterordner enthalten -- aber nicht nach oben.

    yt-dlp erlaubt in der Vorlage ausdruecklich Pfadanteile (etwa
    "%(uploader)s/%(title)s.%(ext)s"). Ein ".." darin fuehrte aus dem
    Zielordner heraus, an jeder Pruefung vorbei.
    """
    teile = re.split(r"[\\/]+", str(tmpl or ""))
    return not (str(tmpl or "").startswith(("/", "\\"))
                or any(t.strip(" .") == "" and t for t in teile))


@app.post("/api/download")
async def add_download(request: Request):
    body = await request.json()
    urls = body.get("urls") or [body.get("url", "")]
    overrides = _overrides_from(body)

    if overrides.get("dest"):
        if not _allowed_path(overrides["dest"]):
            return JSONResponse({"error": "Zielordner nicht erlaubt."}, status_code=403)
    if overrides.get("video_container") not in (None, "", *VIDEO_CONTAINERS):
        return JSONResponse({"error": "Unbekanntes Zielformat."}, status_code=400)
    if overrides.get("playlist_mode") not in (None, "", *PLAYLIST_MODES):
        return JSONResponse({"error": "Unbekannte Playlist-Auswahl."},
                            status_code=400)
    if not _tmpl_ok(overrides.get("output_template")):
        return JSONResponse({"error": "Unzulaessige Dateinamen-Vorlage."},
                            status_code=400)

    # Duplikate erkennt enqueue() selbst -- unter dem Job-Lock, damit auch
    # zwei gleichzeitige Anfragen dieselbe URL nur einmal einreihen.
    added, skipped, abgelehnt = [], [], []
    for raw in urls:
        for line in str(raw).splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                added.append(downloader.enqueue(line, overrides))
            except downloader.Duplikat:
                skipped.append(line)
            except ValueError as e:
                abgelehnt.append({"url": line, "grund": str(e)})
    if abgelehnt and not added and not skipped:
        # Nichts angenommen: dann ist es ein Fehler, keine Teilmeldung.
        return JSONResponse({"error": abgelehnt[0]["grund"],
                             "abgelehnt": abgelehnt}, status_code=400)
    return {"added": added, "skipped": skipped, "abgelehnt": abgelehnt}


@app.get("/api/queue")
async def get_queue():
    return {"jobs": downloader.list_jobs()}


@app.post("/api/cancel/{jid}")
async def cancel(jid: str):
    return {"ok": downloader.cancel(jid)}


@app.post("/api/cancel-all")
async def cancel_all():
    return {"ok": True, "canceled": downloader.cancel_all()}


@app.post("/api/clear-finished")
async def clear_finished():
    downloader.clear_finished()
    return {"ok": True}


# ── Verlauf / Bibliothek ────────────────────────────────────────────────────
@app.get("/api/history")
async def get_history(status: str = "", q: str = "", limit: int = 300,
                      rubrik: str = ""):
    # Einmal abfragen: die Zaehler kosten sonst pro Rubrik einen DB-Zugriff.
    je_rubrik = downloader.rubrik_counts()
    return {"history": downloader.history(status, min(limit, 1000), q.strip(),
                                          rubrik.strip()),
            "counts": downloader.history_counts(),
            "rubriken": [{"key": k, "label": t, "count": je_rubrik.get(k, 0)}
                         for k, t in downloader.RUBRIKEN]}


@app.get("/api/history/{jid}")
async def get_history_one(jid: str):
    rec = downloader.history_one(jid)
    if not rec:
        return JSONResponse({"error": "Nicht gefunden."}, status_code=404)
    return rec


@app.delete("/api/history/{jid}")
async def del_history(jid: str, file: int = 0):
    rec = downloader.delete_history(jid)
    if not rec:
        return JSONResponse({"error": "Nicht gefunden."}, status_code=404)
    removed = []
    if file:
        for f in rec.get("files", []):
            p = _allowed_path(f)
            if p and p.is_file():
                try:
                    p.unlink()
                    removed.append(str(p))
                except OSError:
                    pass
    return {"ok": True, "removed_files": removed}


@app.post("/api/history/{jid}/retry")
async def retry_history(jid: str):
    try:
        job = downloader.retry(jid)
    except downloader.Duplikat:
        return JSONResponse({"error": "Diese Adresse laeuft bereits."},
                            status_code=409)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if not job:
        return JSONResponse({"error": "Nicht gefunden."}, status_code=404)
    return {"ok": True, "job": job}


@app.post("/api/clear-history")
async def clear_history(status: str = ""):
    downloader.clear_history(status)
    return {"ok": True}


# ── Fertige Dateien ansehen / herunterladen ─────────────────────────────────
def _media_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _EXTRA_MIME:
        return _EXTRA_MIME[ext]
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def _file_for(jid: str, idx: int):
    """Datei NUR ueber die Job-ID aufloesen, nie ueber einen Pfad aus der URL."""
    rec = downloader.history_one(jid)
    if not rec:
        return None, "Nicht gefunden."
    files = rec.get("files") or []
    if not 0 <= idx < len(files):
        return None, "Datei nicht im Verlauf."
    p = _allowed_path(files[idx])
    if not p or not p.is_file():
        return None, "Datei existiert nicht mehr."
    return p, None


@app.get("/api/file/{jid}")
async def serve_file(jid: str, request: Request, idx: int = 0, dl: int = 0):
    """Liefert eine fertige Datei aus -- mit Range-Support fuers Spulen.

    Starlette beantwortet Range-Anfragen in dieser Version noch nicht selbst;
    ohne 206-Antwort laedt ein <video>-Element die komplette Datei und laesst
    sich nicht spulen. Darum hier von Hand.
    """
    path, err = _file_for(jid, idx)
    if err:
        return JSONResponse({"error": err}, status_code=404)
    return _ausliefern(path, request, dl)


def _ausliefern(path: Path, request: Request, dl: int = 0):
    size = path.stat().st_size
    media = _media_type(path)
    disp = "attachment" if dl else "inline"
    headers = {
        "Accept-Ranges": "bytes",
        # RFC 5987: Umlaute im Dateinamen ueberstehen so jeden Browser.
        "Content-Disposition": f"{disp}; filename*=UTF-8''{quote(path.name)}",
        "Cache-Control": "private, max-age=3600",
    }

    rng = request.headers.get("range") or request.headers.get("Range")
    start, end = 0, size - 1
    status = 200
    if rng and rng.strip().lower().startswith("bytes="):
        try:
            first, _, last = rng.split("=", 1)[1].split(",")[0].partition("-")
            if first.strip():
                start = int(first)
                if last.strip():
                    end = min(int(last), size - 1)
            elif last.strip():                      # Suffix-Range: letzte N Bytes
                start = max(0, size - int(last))
            if start > end or start >= size:
                return Response(status_code=416,
                                headers={"Content-Range": f"bytes */{size}"})
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        except (ValueError, IndexError):
            start, end, status = 0, size - 1, 200

    length = end - start + 1
    headers["Content-Length"] = str(length)

    def stream():
        remaining = length
        with open(path, "rb") as fh:
            fh.seek(start)
            while remaining > 0:
                chunk = fh.read(min(512 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(stream(), status_code=status,
                             media_type=media, headers=headers)


# ── Ansehen im Browser ──────────────────────────────────────────────────────
def _cache_grenze() -> int:
    """Obergrenze in Bytes; 0 = unbegrenzt (playback raeumt dann nicht auf)."""
    gb = get_settings().get("play_cache_gb", 4)
    try:
        return max(0, int(gb)) * 1024 * 1024 * 1024
    except (TypeError, ValueError):
        return 4 * 1024 * 1024 * 1024


@app.get("/api/play/{jid}")
async def play_info(jid: str, idx: int = 0):
    """Kann der Browser das direkt? Muss vorher umgepackt werden? Geht es gar nicht?"""
    path, err = _file_for(jid, idx)
    if err:
        return JSONResponse({"error": err}, status_code=404)
    plan = playback.plan_fuer(str(path))
    antwort = {"modus": plan["modus"], "grund": plan.get("grund", ""),
               "ziel": plan.get("ziel", ""), "name": path.name}
    if plan["modus"] == "umpacken":
        antwort.update(playback.status(str(path), plan["ziel"]))
    elif plan["modus"] == "direkt":
        antwort.update({"bereit": True, "fortschritt": 100})
    return antwort


@app.post("/api/play/{jid}")
async def play_prepare(jid: str, idx: int = 0):
    """Das Umpacken anstossen. Antwortet sofort, die Arbeit laeuft im Hintergrund."""
    path, err = _file_for(jid, idx)
    if err:
        return JSONResponse({"error": err}, status_code=404)
    plan = playback.vorbereiten(str(path), _cache_grenze())
    return {"modus": plan["modus"], "grund": plan.get("grund", ""),
            "bereit": bool(plan.get("bereit")),
            "fortschritt": plan.get("fortschritt", 0)}


@app.get("/api/play/{jid}/stream")
async def play_stream(jid: str, request: Request, idx: int = 0):
    """Die abspielbare Fassung ausliefern -- mit Range, damit Spulen geht."""
    path, err = _file_for(jid, idx)
    if err:
        return JSONResponse({"error": err}, status_code=404)
    plan = playback.plan_fuer(str(path))
    if plan["modus"] == "direkt":
        return _ausliefern(path, request)
    if plan["modus"] != "umpacken":
        return JSONResponse({"error": plan.get("grund", "Nicht abspielbar.")},
                            status_code=415)
    ablage = playback.cache_pfad(str(path), plan["ziel"])
    if not (ablage.is_file() and ablage.stat().st_size > 0):
        return JSONResponse({"error": "Noch nicht fertig aufbereitet."},
                            status_code=409)
    os.utime(ablage, None)              # zuletzt benutzt -> faellt spaeter raus
    return _ausliefern(ablage, request)


@app.get("/api/playcache")
async def playcache_info():
    return {"bytes": playback.cache_groesse(), "grenze": _cache_grenze()}


@app.delete("/api/playcache")
async def playcache_clear():
    return {"ok": True, "freigegeben": playback.cache_leeren()}


# ── Zugangsdaten je Seite ───────────────────────────────────────────────────
@app.get("/api/logins")
async def get_logins():
    return {"logins": logins.list_logins()}


@app.put("/api/logins")
async def put_login(request: Request):
    body = await request.json()
    try:
        eintrag = logins.save_login(
            body.get("domain", ""), body.get("username", ""),
            body.get("password", ""), body.get("videopassword", ""),
            body.get("note", ""))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"ok": True, "login": eintrag}


@app.delete("/api/logins/{domain}")
async def del_login(domain: str):
    if not logins.delete_login(domain):
        return JSONResponse({"error": "Nicht gefunden."}, status_code=404)
    return {"ok": True}


# ── Altbestand einsortieren ─────────────────────────────────────────────────
@app.get("/api/organize")
async def organize_preview():
    """Trockenlauf -- zeigt, was wohin wandern wuerde. Aendert nichts."""
    return downloader.organize_plan()


@app.post("/api/organize")
async def organize_run(request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:                     # noqa: BLE001 - Aufruf ohne Rumpf
        pass
    return downloader.organize_run(body.get("jids") or None)


# ── Einstellungen ───────────────────────────────────────────────────────────
@app.get("/api/settings")
async def read_settings():
    s = get_settings()
    s["_cookies_present"] = COOKIES_FILE.exists()
    s["_version"] = VERSION
    return s


@app.put("/api/settings")
async def write_settings(request: Request):
    body = await request.json()
    # Dieselbe Pruefung wie bei /api/download: eine Vorlage mit ".." schriebe
    # sonst global aus dem Zielordner heraus.
    if "output_template" in body and not _tmpl_ok(body.get("output_template")):
        return JSONResponse({"error": "Unzulaessige Dateinamen-Vorlage."},
                            status_code=400)
    if body.get("default_dest") and not _allowed_path(body["default_dest"]):
        return JSONResponse({"error": "Standard-Zielordner nicht erlaubt."},
                            status_code=403)
    return save_settings(body)


# ── Cookies (Logins / Altersfreigaben) ──────────────────────────────────────
@app.get("/api/cookies")
async def cookies_status():
    if not COOKIES_FILE.exists():
        return {"present": False}
    st = COOKIES_FILE.stat()
    return {"present": True, "size": st.st_size, "mtime": int(st.st_mtime)}


COOKIE_MAX = 4 * 1024 * 1024


@app.post("/api/cookies")
async def upload_cookies(file: UploadFile):
    data = await file.read(COOKIE_MAX + 1)
    if len(data) > COOKIE_MAX:
        # Lieber ablehnen als stillschweigend abschneiden -- eine halbe
        # Cookie-Datei fuehrt zu schwer erklaerbaren Login-Fehlern.
        return JSONResponse({"error": "Datei ist groesser als 4 MB."},
                            status_code=413)
    text = data.decode("utf-8", "replace")
    if "\t" not in text:
        return JSONResponse(
            {"error": "Keine Cookie-Datei im Netscape-Format (Tab-getrennt)."},
            status_code=400)
    COOKIES_FILE.write_text(text, encoding="utf-8")
    try:
        os.chmod(COOKIES_FILE, 0o600)
    except OSError:
        pass
    return {"ok": True, "size": len(data)}


@app.delete("/api/cookies")
async def delete_cookies():
    COOKIES_FILE.unlink(missing_ok=True)
    return {"ok": True}


# ── NAS-Freigaben (SMB/CIFS) ────────────────────────────────────────────────
@app.get("/api/shares")
async def get_shares():
    return {"shares": shares.list_shares()}


def _invalidate_roots():
    global _roots_cache
    _roots_cache = None


@app.post("/api/shares")
async def add_share(request: Request):
    b = await request.json()
    try:
        res = shares.add_share(
            b.get("name", ""), b.get("server", ""), b.get("share", ""),
            b.get("username", ""), b.get("password", ""), b.get("domain", ""))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    _invalidate_roots()
    return res


@app.delete("/api/shares/{name}")
async def del_share(name: str):
    shares.remove_share(name)
    _invalidate_roots()
    return {"ok": True}


# ── Host-Ordner durchsuchen (Ziel frei waehlbar) ────────────────────────────
@app.get("/api/roots")
async def roots():
    return {"roots": [{"label": l, "path": p} for l, p in _roots()],
            "hinweis": _roots_hinweis()}


@app.get("/api/browse")
async def browse(path: str = ""):
    if not path:
        return {"path": "", "parent": None, "dirs": [], "roots":
                [{"label": l, "path": p} for l, p in _roots()]}
    target = _allowed_path(path)
    if not target:
        return JSONResponse({"error": "Pfad nicht erlaubt."}, status_code=403)
    if not target.is_dir():
        return JSONResponse({"error": "Kein Verzeichnis."}, status_code=400)
    dirs = []
    try:
        for d in sorted(target.iterdir(), key=lambda x: x.name.lower()):
            if d.is_dir() and not d.name.startswith("."):
                dirs.append({"name": d.name, "path": str(d)})
    except PermissionError:
        return JSONResponse({"error": "Kein Zugriff."}, status_code=403)
    # An einer Wurzel gibt es kein "nach oben" -- frueher wurde hier trotzdem
    # ein Elternpfad angeboten, der dann konsequent mit 403 endete.
    parent = None
    if not _is_root(target) and _allowed_path(str(target.parent)):
        parent = str(target.parent)
    return {"path": str(target), "parent": parent, "dirs": dirs}


@app.post("/api/mkdir")
async def mkdir(request: Request):
    b = await request.json()
    base = _allowed_path(b.get("path", ""))
    name = os.path.basename(b.get("name", "").strip())
    if not name or name in (".", "..") or not base:
        return JSONResponse({"error": "Ungueltig."}, status_code=400)
    new = base / name
    try:
        new.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"path": str(new)}


# ── WebSocket: Live-Fortschritt ─────────────────────────────────────────────
@app.websocket("/ws")
async def ws(websocket: WebSocket):
    if not auth.read_session(websocket.cookies.get("session", "")):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    q: asyncio.Queue = asyncio.Queue(maxsize=2000)

    def _cb(event):
        if _loop:
            # Aus Worker-Threads -> Eventloop. Bei vollem Puffer lieber ein
            # Fortschritts-Update verwerfen als den Download auszubremsen.
            def _put():
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass
            _loop.call_soon_threadsafe(_put)

    downloader.subscribe(_cb)
    try:
        await websocket.send_json({"type": "snapshot",
                                   "jobs": downloader.list_jobs(),
                                   "version": VERSION})
        while True:
            await websocket.send_json(await q.get())
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        downloader.unsubscribe(_cb)


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

# ============================================================================
# Erdacht und programmiert von Fabian Hammer.
# (c) 2026 Fabian Hammer - Alle Rechte vorbehalten. Geistiges Eigentum.
# ============================================================================
"""Zentrale Pfade & persistente Konfiguration.

WICHTIG: Alles Persistente (Settings, Nutzer, NAS-Zugaenge, Verlauf) liegt in
CONFIG_DIR. Dieses Verzeichnis wird per Bind-Mount aus dem Host eingehaengt und
von 'manage.sh update' NIEMALS ueberschrieben. Der App-Code (dieser Ordner)
wird bei jedem Update ersetzt -- die Nutzerdaten bleiben davon unberuehrt.
"""
import base64
import hashlib
import json
import os
import secrets
import threading
from pathlib import Path

# ── Pfade (per ENV aus docker-compose steuerbar) ────────────────────────────
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
DOWNLOADS_DIR = Path(os.environ.get("DOWNLOADS_DIR", "/downloads"))
# Host-Dateisystem read/write ins Container gemountet -> Ziel frei waehlbar.
# Was dort auf dem Host eingehaengt ist (HOSTFS_MOUNT aus der .env), steht seit
# 2.5.1 als HOSTFS_HOST_PATH im Container -- nur zur Anzeige in der GUI.
HOSTFS_DIR = Path(os.environ.get("HOSTFS_DIR", "/hostfs"))
HOSTFS_HOST_PATH = os.environ.get("HOSTFS_HOST_PATH", "").strip()


def _download_roots() -> list:
    """DOWNLOAD_ROOTS: Komma-Liste erlaubter Wurzeln unterhalb von HOSTFS_DIR.

    Leer = das ganze eingehaengte Host-Dateisystem ist waehlbar (wie bisher).
    Jeder Eintrag ist ein Pfad relativ zum Mount ("/medien" oder "medien")
    oder bereits mit HOSTFS_DIR vorangestellt ("/hostfs/medien"). ".." fliegt
    raus -- sonst liesse sich die Einschraenkung per Konfiguration selbst
    wieder aushebeln.
    """
    roh = os.environ.get("DOWNLOAD_ROOTS", "")
    wurzeln = []
    for teil in roh.split(","):
        teil = teil.strip().replace("\\", "/")
        if not teil:
            continue
        if teil == str(HOSTFS_DIR) or teil.startswith(str(HOSTFS_DIR) + "/"):
            teil = teil[len(str(HOSTFS_DIR)):]
        segmente = [x for x in teil.split("/") if x and x != "."]
        if any(x == ".." for x in segmente):
            continue
        pfad = HOSTFS_DIR.joinpath(*segmente) if segmente else HOSTFS_DIR
        if pfad not in wurzeln:
            wurzeln.append(pfad)
    return wurzeln


DOWNLOAD_ROOTS = _download_roots()

# Downloads von Adressen im eigenen Netz (10/8, 192.168/16, 127/8, ::1, ...)
# werden abgelehnt: yt-dlp wuerde sonst mit den Rechten des Containers
# interne Dienste ansprechen. Nur fuer Tests oder bewusst gewollte
# Heimnetz-Quellen per ENV freischaltbar.
DOWNLOAD_ALLOW_PRIVATE = os.environ.get("DOWNLOAD_ALLOW_PRIVATE", "").strip().lower() \
    in ("1", "true", "on", "yes", "ja")
# Wohin CIFS/SMB-Netzlaufwerke (NAS) eingehaengt werden.
SHARES_DIR = Path(os.environ.get("SHARES_DIR", "/mnt/shares"))

CONFIG_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
SHARES_DIR.mkdir(parents=True, exist_ok=True)

SETTINGS_FILE = CONFIG_DIR / "settings.json"
# Zugangsdaten fuer Seiten, die einen Login verlangen (Altersfreigaben,
# Bezahlportale). Passwoerter darin sind verschluesselt, nie im Klartext.
LOGINS_FILE = CONFIG_DIR / "logins.json"
USERS_FILE = CONFIG_DIR / "users.json"
SHARES_FILE = CONFIG_DIR / "shares.json"
HISTORY_DB = CONFIG_DIR / "history.sqlite"
SECRET_FILE = CONFIG_DIR / "secret.key"
COOKIES_FILE = CONFIG_DIR / "cookies.txt"
# Arbeitsverzeichnis fuer Fragmente/ffmpeg-Zwischendateien (siehe local_temp).
TEMP_DIR = CONFIG_DIR / "tmp"

# Container, in die verlustfrei umgepackt werden kann (yt-dlp Remuxer).
VIDEO_CONTAINERS = ("auto", "mkv", "mp4", "webm", "mov")
# Was bei einer URL passieren soll, die zu einer Playlist gehoert.
PLAYLIST_MODES = ("playlist", "single")
# Browser-Tarnung: auto | immer | aus (siehe DEFAULT_SETTINGS)
TARNUNG_MODI = ("auto", "immer", "aus")

_lock = threading.RLock()


def get_secret() -> str:
    """Stabiler Session-/Krypto-Schluessel, einmalig erzeugt & persistiert."""
    if SECRET_FILE.exists():
        return SECRET_FILE.read_text().strip()
    key = secrets.token_hex(32)
    SECRET_FILE.write_text(key)
    try:
        os.chmod(SECRET_FILE, 0o600)
    except OSError:
        pass
    return key


def _key() -> bytes:
    return hashlib.sha256(get_secret().encode()).digest()


def _xor(data: bytes) -> bytes:
    k = _key()
    return bytes(b ^ k[i % len(k)] for i, b in enumerate(data))


def encrypt_secret(text: str) -> str:
    """Ein Geheimnis mit dem Instanz-Schluessel ablegbar machen.

    Schuetzt gegen den Blick in die Datei, nicht gegen einen Angreifer, der
    ohnehin schon secret.key lesen kann -- der kaeme an alles. Genau derselbe
    Weg wie bei den NAS-Freigaben, damit es nur eine Stelle dafuer gibt.
    """
    return base64.b64encode(_xor(str(text or "").encode())).decode()


def decrypt_secret(token: str) -> str:
    try:
        return _xor(base64.b64decode(token)).decode()
    except Exception:
        return ""


DEFAULT_SETTINGS = {
    # Standard-Zielordner (relativ zu DOWNLOADS_DIR-Mount oder absolut im Container)
    "default_dest": str(DOWNLOADS_DIR),
    # Dateinamen-Vorlage (yt-dlp Output-Template)
    "output_template": "%(title)s [%(id)s].%(ext)s",
    # Im Zielordner nach Quelle/Art/Jahr/Monat einsortieren,
    # z.B. Youtube/video/2026/09. Aus = alles flach in den Zielordner.
    "organize_subdirs": True,
    "format_mode": "best",          # best | audio | custom  (mp4 = Altwert)
    "video_container": "auto",      # auto | mkv | mp4 | webm | mov (verlustfrei)
    "audio_format": "mp3",          # mp3 | m4a | opus | flac | wav
    "audio_quality": "0",           # 0=best
    "max_quality": "0",             # 0=unbegrenzt, sonst z.B. 1080
    "embed_metadata": True,
    "embed_thumbnail": True,
    "embed_subs": False,
    "write_subs": False,
    "sub_langs": "en,de",
    "sponsorblock": False,          # Werbe-/Intro-Segmente entfernen
    "concurrent_downloads": 3,      # gleichzeitige Jobs
    "concurrent_fragments": 4,      # yt-dlp -N (Fragmente pro Job)
    "rate_limit": "",               # z.B. "5M" -> 5 MB/s, leer = unbegrenzt
    "custom_format": "",            # eigener yt-dlp -f Ausdruck
    "restrict_filenames": False,
    "playlist_items": "",           # z.B. "1-10" leer=alles
    # Verweist eine URL auf eine Playlist (…&list=…), wird standardmaessig die
    # ganze Liste geladen. "single" laedt stattdessen nur den verlinkten Titel
    # -- bei YT-Musik ist das ein Lied, kein Video.
    "playlist_mode": "playlist",    # playlist | single
    # Fragmente/Merge lokal zwischenspeichern statt direkt aufs Ziel zu schreiben.
    # Bringt bei NAS-/SMB-Zielen viel (1x Kopie statt 3x Netzwerk-I/O), braucht
    # aber lokalen Plattenplatz in CONFIG_DIR/tmp.
    "local_temp": False,
    # Cookie-Datei (Netscape-Format) fuer Logins/Altersfreigaben nutzen.
    # Fuer die meisten Seiten reicht inzwischen ein Eintrag unter
    # "Zugangsdaten je Seite" -- Cookies bleiben fuer Youtube der einzige Weg.
    "use_cookies": False,
    # Obergrenze fuer die Zwischendateien des Players (GB). 0 = unbegrenzt:
    # dann wird NICHT aufgeraeumt (frueher hiess 0 versehentlich "alles weg").
    "play_cache_gb": 4,
    # Zeitlimit je Auftrag in Stunden. Haengt ein Download laenger (Portal
    # drosselt, Verbindung eingeschlafen), wird er abgebrochen, als Fehler
    # verbucht und der Worker wird frei; der Teil-Download bleibt fuer
    # "Fortsetzen" liegen. 0 = kein Limit.
    "job_timeout_h": 6,
    # Browser-Tarnung (yt-dlp "impersonate" ueber curl_cffi). Manche Portale
    # -- PornHub, Cloudflare-geschuetzte Seiten -- weisen die TLS-Signatur von
    # urllib mit HTTP 403 ab, obwohl die Seite im Browser laeuft.
    #   auto  = Tarnung, wo der Extraktor sie verlangt; ein mit 403 abgewiesener
    #           Auftrag wird einmal als Chrome wiederholt
    #   immer = jede Anfrage als Chrome (hilft bei unbekannten Sperren,
    #           kostet u. U. Tempo)
    #   aus   = keine Wiederholung
    "browser_tarnung": "auto",      # auto | immer | aus
    # Abgeschlossene Jobs nach X Minuten aus dem Arbeitsspeicher raeumen
    # (Verlauf bleibt in der Datenbank erhalten).
    "job_retention_min": 30,
}


def load_json(path: Path, default):
    with _lock:
        if not path.exists():
            return json.loads(json.dumps(default))
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return json.loads(json.dumps(default))


def save_json(path: Path, data):
    with _lock:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


def _coerce(key: str, value):
    """Auf den Typ des Defaults zwingen.

    Die WebGUI schickt Zahlenfelder als Strings ("3" statt 3). Ohne diese
    Normalisierung landen Strings in der settings.json und jeder Leser muss
    selbst casten -- genau da entstehen spaeter TypeErrors.
    """
    default = DEFAULT_SETTINGS.get(key)
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "on", "yes", "ja")
        return bool(value)
    if isinstance(default, int):
        try:
            return int(str(value).strip() or 0)
        except (TypeError, ValueError):
            return default
    return "" if value is None else str(value).strip()


def _migrate(s: dict) -> dict:
    """Altbestand reparieren, damit bestehende Installationen sauber starten."""
    # 1) Der frueher einzige Weg zu MKV war, "mkv" ins yt-dlp-Formatfeld zu
    #    tippen -- dort ist es aber ein ungueltiger Formatausdruck und wurde
    #    stillschweigend ignoriert. Jetzt gibt es dafuer video_container.
    cf = str(s.get("custom_format", "")).strip().lower()
    if cf in VIDEO_CONTAINERS[1:]:
        if s.get("video_container", "auto") == "auto":
            s["video_container"] = cf
        s["custom_format"] = ""
        if s.get("format_mode") == "custom":
            s["format_mode"] = "best"
    # 2) Alter Modus "mp4" ist jetzt "bestes Video" + Container mp4.
    if s.get("format_mode") == "mp4":
        s["format_mode"] = "best"
        if s.get("video_container", "auto") == "auto":
            s["video_container"] = "mp4"
    if s.get("video_container") not in VIDEO_CONTAINERS:
        s["video_container"] = "auto"
    if s.get("format_mode") not in ("best", "audio", "custom"):
        s["format_mode"] = "best"
    if s.get("playlist_mode") not in PLAYLIST_MODES:
        s["playlist_mode"] = "playlist"
    if s.get("browser_tarnung") not in TARNUNG_MODI:
        s["browser_tarnung"] = "auto"
    return s


def get_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    stored = load_json(SETTINGS_FILE, {})
    for k in DEFAULT_SETTINGS:
        if k in stored:
            s[k] = _coerce(k, stored[k])
    return _migrate(s)


def save_settings(new: dict) -> dict:
    s = get_settings()
    for k in DEFAULT_SETTINGS:
        if k in new:
            s[k] = _coerce(k, new[k])
    s = _migrate(s)
    save_json(SETTINGS_FILE, s)
    return s

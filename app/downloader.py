# ============================================================================
# Erdacht und programmiert von Fabian Hammer.
# (c) 2026 Fabian Hammer - Alle Rechte vorbehalten. Geistiges Eigentum.
# ============================================================================
"""Download-Engine auf Basis von yt-dlp.

- Jobs landen in einer Queue; mehrere Worker (Anzahl = settings.concurrent_downloads)
  arbeiten sie parallel ab.
- Fortschritt wird ueber einen Callback live an die WebGUI (WebSocket) gemeldet.
- Abgeschlossene Jobs wandern in einen SQLite-Verlauf (CONFIG_DIR/history.sqlite),
  getrennt abrufbar nach fertig / fehlgeschlagen / abgebrochen.
- Unterstuetzt ALLE von yt-dlp abgedeckten Seiten (>1800), Playlists, reine
  Audio-Extraktion, verlustfreies Umpacken in mkv/mp4/webm/mov, Qualitaets-Cap,
  SponsorBlock, Untertitel, Metadaten/Thumbs.
"""
import ipaddress
import json
import os
import queue
import re
import shutil
import socket
import sqlite3
import struct
import threading
import time
import unicodedata
import uuid
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit

import yt_dlp
import yt_dlp.dependencies
from yt_dlp.networking.impersonate import ImpersonateTarget
from yt_dlp.utils import DownloadCancelled

from config import (COOKIES_FILE, DOWNLOAD_ALLOW_PRIVATE, HISTORY_DB,
                    PLAYLIST_MODES, TARNUNG_MODI, TEMP_DIR, VIDEO_CONTAINERS,
                    get_settings)
from logins import login_for


class Duplikat(ValueError):
    """Dieselbe Adresse laeuft oder wartet bereits."""

_jobs = {}                 # id -> job dict (aktive + kuerzlich beendete)
_jobs_lock = threading.RLock()
_q = queue.Queue()
_subscribers = set()       # set von Callback-Funktionen (WebSocket-Push)
_subs_lock = threading.Lock()
_workers = []
_worker_target = 0
_example_started = False

# Fortschritt wird hoechstens alle PUBLISH_INTERVAL Sekunden gesendet. yt-dlp
# ruft den Progress-Hook pro Datei-Chunk auf -- ungebremst waeren das hunderte
# WebSocket-Nachrichten pro Sekunde und Job, was die GUI ausbremst.
PUBLISH_INTERVAL = 0.25

# Aktive Zustaende: alles andere gilt als abgeschlossen.
ACTIVE = ("queued", "downloading", "processing", "canceling")

# Diese Container kann kein verbreiteter Browser nativ abspielen -- die GUI
# bietet dafuer den Download statt eines toten Players an.
BROWSER_PLAYABLE = {".mp4", ".m4v", ".webm", ".ogv", ".ogg", ".mov",
                    ".mp3", ".m4a", ".opus", ".wav", ".flac", ".aac"}


# ── Verlauf (SQLite) ────────────────────────────────────────────────────────
HISTORY_COLUMNS = [
    ("id", "TEXT PRIMARY KEY"), ("url", "TEXT"), ("title", "TEXT"),
    ("filename", "TEXT"), ("dest", "TEXT"), ("status", "TEXT"),
    ("error", "TEXT"), ("filesize", "INTEGER"), ("finished", "INTEGER"),
    # Ab v2: finale Dateien (JSON-Liste), Optionen fuer die Wiederaufnahme,
    # yt-dlp-Protokoll zur Fehlersuche.
    ("files", "TEXT"), ("opts", "TEXT"), ("log", "TEXT"),
    ("duration", "INTEGER"), ("ext", "TEXT"),
    # Ab v2.1: Herkunft des Downloads -- "quelle" ist der Anzeige- und
    # Ordnername (Youtube, YT-Musik, beispiel.de), "rubrik" gruppiert die
    # Bibliothek (youtube, musik, mediathek, social, erwachsen, ...).
    ("quelle", "TEXT"), ("rubrik", "TEXT"),
]
_COLS = [c for c, _ in HISTORY_COLUMNS]
_db_lock = threading.Lock()
_schema_ready = False


def _ensure_schema(conn):
    """Tabelle anlegen und fehlende Spalten aelterer Versionen ergaenzen.

    Laeuft bewusst nur EINMAL pro Prozess: ein Video-Player feuert beim Spulen
    dutzende Range-Anfragen, und jede davon schlaegt auf den Verlauf durch.
    """
    global _schema_ready
    conn.execute("CREATE TABLE IF NOT EXISTS history(%s)" %
                 ", ".join(f"{c} {t}" for c, t in HISTORY_COLUMNS))
    have = {r[1] for r in conn.execute("PRAGMA table_info(history)")}
    for col, typ in HISTORY_COLUMNS:
        if col not in have:
            conn.execute(f"ALTER TABLE history ADD COLUMN {col} "
                         f"{typ.replace(' PRIMARY KEY', '')}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hist_status "
                 "ON history(status, finished DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hist_rubrik "
                 "ON history(rubrik, finished DESC)")
    # Bestehende Eintraege bekommen ihre Herkunft nachtraeglich -- sonst
    # stuenden alle Altdownloads unter "Sonstiges", obwohl die URL sie
    # eindeutig zuordnet.
    offen = conn.execute("SELECT id, url FROM history "
                         "WHERE rubrik IS NULL OR rubrik = ''").fetchall()
    for jid, url in offen:
        quelle, rubrik = _quelle_und_rubrik(url)
        conn.execute("UPDATE history SET quelle=?, rubrik=? WHERE id=?",
                     (quelle, rubrik, jid))
    conn.commit()
    _schema_ready = True


def _db():
    conn = sqlite3.connect(HISTORY_DB, timeout=15)
    if not _schema_ready:
        conn.execute("PRAGMA journal_mode=WAL")
        _ensure_schema(conn)
    return conn


def _row(r) -> dict:
    d = dict(zip(_COLS, r))
    try:
        d["files"] = json.loads(d.get("files") or "[]")
    except ValueError:
        d["files"] = []
    try:
        d["opts"] = json.loads(d.get("opts") or "{}")
    except ValueError:
        d["opts"] = {}
    d["playable"] = bool(d["files"]) and any(
        os.path.splitext(f)[1].lower() in BROWSER_PLAYABLE for f in d["files"])
    # Nur die erste Datei pruefen: auf einem SMB-Mount ist jedes stat() ein
    # Netzwerk-Roundtrip, und die Liste hat pro Playlist viele Eintraege.
    d["exists"] = bool(d["files"]) and os.path.isfile(d["files"][0])
    return d


def history(status: str = "", limit: int = 300, query: str = "",
            rubrik: str = "") -> list:
    sql = f"SELECT {','.join(_COLS)} FROM history"
    args, where = [], []
    if status and status != "all":
        where.append("status = ?")
        args.append(status)
    if rubrik and rubrik != "all":
        where.append("rubrik = ?")
        args.append(rubrik)
    if query:
        where.append("(title LIKE ? OR url LIKE ? OR filename LIKE ?)")
        args += [f"%{query}%"] * 3
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY finished DESC LIMIT ?"
    args.append(int(limit))
    with _db_lock:
        conn = _db()
        rows = conn.execute(sql, args).fetchall()
        conn.close()
    return [_row(r) for r in rows]


def history_counts() -> dict:
    with _db_lock:
        conn = _db()
        rows = conn.execute("SELECT status, COUNT(*) FROM history "
                            "GROUP BY status").fetchall()
        conn.close()
    counts = {"done": 0, "error": 0, "canceled": 0}
    for st, n in rows:
        counts[st] = counts.get(st, 0) + n
    counts["all"] = sum(n for _, n in rows)
    return counts


def rubrik_counts() -> dict:
    """Wie viele Eintraege je Rubrik -- fuer die Filterleiste der Bibliothek."""
    with _db_lock:
        conn = _db()
        rows = conn.execute("SELECT rubrik, COUNT(*) FROM history "
                            "GROUP BY rubrik").fetchall()
        conn.close()
    counts = {}
    for rub, n in rows:
        counts[rub or "sonstiges"] = counts.get(rub or "sonstiges", 0) + n
    return counts


def history_one(jid: str):
    with _db_lock:
        conn = _db()
        r = conn.execute(f"SELECT {','.join(_COLS)} FROM history WHERE id=?",
                         (jid,)).fetchone()
        conn.close()
    return _row(r) if r else None


def _record(job):
    files = sorted(job.get("files") or [])
    row = (
        job["id"], job["url"], job.get("title", ""), job.get("filename", ""),
        job.get("dest", ""), job["status"], job.get("error", ""),
        int(job.get("filesize") or 0), int(time.time()),
        json.dumps(files, ensure_ascii=False),
        json.dumps(job.get("overrides") or {}, ensure_ascii=False),
        "\n".join(job.get("log") or [])[-8000:],
        int(job.get("duration") or 0),
        os.path.splitext(files[0])[1].lstrip(".").lower() if files else "",
        *_quelle_und_rubrik(job.get("url", "")),
    )
    with _db_lock:
        conn = _db()
        conn.execute("INSERT OR REPLACE INTO history(%s) VALUES(%s)" %
                     (",".join(_COLS), ",".join("?" * len(_COLS))), row)
        conn.commit()
        conn.close()


def delete_history(jid: str):
    """Loescht einen Verlaufseintrag und liefert ihn zurueck (fuer Dateiloeschung)."""
    rec = history_one(jid)
    if rec:
        with _db_lock:
            conn = _db()
            conn.execute("DELETE FROM history WHERE id=?", (jid,))
            conn.commit()
            conn.close()
        with _jobs_lock:
            _jobs.pop(jid, None)
        _broadcast({"type": "history"})
    return rec


def clear_history(status: str = ""):
    """Leert den kompletten Verlauf oder nur eine Rubrik."""
    with _db_lock:
        conn = _db()
        if status and status != "all":
            conn.execute("DELETE FROM history WHERE status=?", (status,))
        else:
            conn.execute("DELETE FROM history")
        conn.commit()
        conn.close()
    _broadcast({"type": "history"})


# ── Pub/Sub fuer Live-Fortschritt ───────────────────────────────────────────
def subscribe(cb):
    with _subs_lock:
        _subscribers.add(cb)


def unsubscribe(cb):
    with _subs_lock:
        _subscribers.discard(cb)


def _broadcast(event: dict):
    with _subs_lock:
        subs = list(_subscribers)
    for cb in subs:
        try:
            cb(event)
        except Exception:
            pass


def _publish_job(job, force=True):
    """Sendet den Job an alle Clients -- gedrosselt, ausser bei Statuswechseln."""
    now = time.monotonic()
    if not force:
        if now - job.get("_last_pub", 0.0) < PUBLISH_INTERVAL:
            return
    job["_last_pub"] = now
    _broadcast({"type": "job", "job": _public(job)})


def _public(job) -> dict:
    d = {k: job.get(k) for k in (
        "id", "url", "title", "status", "progress", "speed", "eta",
        "downloaded", "total", "filename", "dest", "error", "playlist_index",
        "playlist_count")}
    d["eta"] = job.get("eta_smooth")
    return d


# ── Job-Verwaltung ──────────────────────────────────────────────────────────
def _new_job(url: str, overrides: dict, jid: str = "") -> dict:
    return {
        "id": jid or uuid.uuid4().hex[:12], "url": url.strip(),
        "status": "queued", "progress": 0.0, "speed": 0, "eta": None,
        "eta_smooth": None, "downloaded": 0, "total": 0,
        "title": url.strip(), "filename": "", "error": "",
        "dest": overrides.get("dest", ""), "overrides": dict(overrides),
        "playlist_index": None, "playlist_count": None,
        "files": [], "log": deque(maxlen=120), "filesize": 0, "duration": 0,
    }


def enqueue(url: str, overrides: dict = None) -> dict:
    """Einen Auftrag einreihen.

    Wirft ValueError, wenn die Adresse nicht erlaubt ist (kein http/https,
    Ziel im eigenen Netz), und Duplikat, wenn sie bereits laeuft oder wartet.
    Beides wird HIER geprueft und nicht im Aufrufer: so gilt es fuer jeden
    Weg in die Warteschlange -- auch fuer "Fortsetzen" aus dem Verlauf.
    """
    grund = url_pruefen(url)
    if grund:
        raise ValueError(grund)
    job = _new_job(url, overrides or {})
    # Den Unterpfad schon beim Einreihen festlegen, nicht erst beim Start:
    # sonst haette ein Job, der in der WARTESCHLANGE abgebrochen wird, gar
    # keinen -- und waere beim Fortsetzen nicht mehr von echtem Altbestand zu
    # unterscheiden, der bewusst flach bleiben soll.
    o = job["overrides"]
    if "subdir" not in o:
        s = get_settings()
        mode = o.get("format_mode", s["format_mode"])
        if mode == "mp4":                   # Altwert aus Version 1
            mode = "best"
        o["subdir"] = (_subdir_for(url, mode)
                       if s.get("organize_subdirs", True) else "")
    with _jobs_lock:
        # Pruefung und Eintrag unter EINEM Lock: zwei gleichzeitige Anfragen
        # mit derselben URL sahen sich frueher beide als "neu".
        if any(j["url"] == job["url"] and j["status"] in ACTIVE
               for j in _jobs.values()):
            raise Duplikat(job["url"])
        _jobs[job["id"]] = job
    _q.put(job["id"])
    _publish_job(job)
    _ensure_workers()
    return _public(job)


def active_urls() -> set:
    with _jobs_lock:
        return {j["url"] for j in _jobs.values() if j["status"] in ACTIVE}


def retry(jid: str):
    """Erneut einreihen -- fuer fehlgeschlagene UND abgebrochene Downloads.

    yt-dlp setzt einen abgebrochenen Download an der .part-Datei fort, sofern
    Ziel und Format identisch sind. Genau dafuer liegen die urspruenglichen
    Optionen im Verlauf.
    """
    rec = history_one(jid)
    if not rec:
        return None
    opts = dict(rec.get("opts") or {})
    # Verlaufseintraege von VOR der Unterordner-Ablage kennen keinen Unterpfad.
    # Fuer sie bleibt es beim flachen Zielordner: genau dort liegt ihre
    # .part-Datei, und nur dort kann die Wiederaufnahme fortsetzen.
    # (Ein bereits in der Warteschlange abgebrochener Job hat aus demselben
    # Grund kein subdir und landet deshalb ebenfalls flach -- er hat nie
    # geladen, es geht dabei nichts verloren.)
    opts.setdefault("subdir", "")
    return enqueue(rec["url"], opts)


def cancel(jid: str) -> bool:
    with _jobs_lock:
        job = _jobs.get(jid)
        if not job or job["status"] not in ACTIVE:
            return False
        job["cancel"] = True
        if job["status"] == "queued":
            # Noch nicht gestartet -> sofort als abgebrochen verbuchen.
            job["status"] = "canceled"
            job["error"] = "Vor dem Start abgebrochen."
            _record(job)
            _publish_job(job)
        else:
            # Laeuft: Zwischenzustand anzeigen, damit der Klick sichtbar
            # ankommt. Der Abbruch greift beim naechsten Fortschritt-Callback.
            job["status"] = "canceling"
            _publish_job(job)
    return True


def cancel_all() -> int:
    with _jobs_lock:
        ids = [j["id"] for j in _jobs.values() if j["status"] in ACTIVE]
    return sum(1 for jid in ids if cancel(jid))


def list_jobs() -> list:
    with _jobs_lock:
        return [_public(j) for j in _jobs.values() if j["status"] in ACTIVE]


def clear_finished():
    with _jobs_lock:
        gone = [j["id"] for j in _jobs.values() if j["status"] not in ACTIVE]
        for jid in gone:
            _jobs.pop(jid, None)
    for jid in gone:
        _broadcast({"type": "removed", "id": jid})


def _example():
    """Beendete Jobs aus dem Arbeitsspeicher raeumen (Verlauf bleibt in der DB)."""
    while True:
        time.sleep(60)
        try:
            keep = max(1, int(get_settings()["job_retention_min"])) * 60
        except Exception:
            keep = 1800
        cutoff = time.time() - keep
        with _jobs_lock:
            gone = [j["id"] for j in _jobs.values()
                    if j["status"] not in ACTIVE and j.get("ended", 0) < cutoff]
            for jid in gone:
                _jobs.pop(jid, None)
        for jid in gone:
            _broadcast({"type": "removed", "id": jid})


# ── yt-dlp Protokoll pro Job ────────────────────────────────────────────────
class _JobLogger:
    """Faengt yt-dlp-Meldungen pro Job ab.

    Ohne das steht bei einem Fehlschlag nur eine generische Meldung in der GUI.
    Mit Protokoll sieht man die echte Ursache (Geosperre, Login noetig,
    Video privat, ffmpeg-Fehler) und kann gezielt reagieren.
    """

    def __init__(self, job):
        self.job = job

    def _add(self, msg, level):
        msg = str(msg).strip()
        if not msg or msg.startswith("[debug] "):
            return
        self.job["log"].append(f"{time.strftime('%H:%M:%S')} {level} {msg}")
        if level == "ERROR":
            # Letzte echte Fehlermeldung merken -- schlaegt die generische.
            self.job["last_error"] = msg.replace("ERROR: ", "").strip()[:500]

    def debug(self, msg):
        pass

    def info(self, msg):
        self._add(msg, "info")

    def warning(self, msg):
        self._add(msg, "WARN")

    def error(self, msg):
        self._add(msg, "ERROR")


# ── Quellen, Ablage in Unterordnern und Rubriken ────────────────────────────
# Je Domain: (Ordnername, Rubrik). Der Ordnername taucht im Dateisystem auf,
# die Rubrik gruppiert den Verlauf in der WebGUI. Schluessel ist die Domain,
# unter der die Seite erreichbar ist; Subdomains wie www., m. oder de. treffen
# ueber die Rueckwaerts-Suche in _quelle_und_rubrik automatisch mit.
#
# Reihenfolge spielt keine Rolle -- gesucht wird von der laengsten
# Uebereinstimmung her, damit music.youtube.com nicht bei youtube.com landet.
_QUELLEN = {
    # Youtube und Youtube Music getrennt -- ausdruecklicher Wunsch
    "music.youtube.com": ("YT-Musik", "musik"),
    "youtube.com": ("Youtube", "youtube"),
    "youtu.be": ("Youtube", "youtube"),
    "youtube-nocookie.com": ("Youtube", "youtube"),
    # Video
    "vimeo.com": ("Vimeo", "video"),
    "dailymotion.com": ("Dailymotion", "video"),
    "dai.ly": ("Dailymotion", "video"),
    "twitch.tv": ("Twitch", "video"),
    "rumble.com": ("Rumble", "video"),
    "odysee.com": ("Odysee", "video"),
    "nebula.tv": ("Nebula", "video"),
    "bilibili.com": ("Bilibili", "video"),
    "vk.com": ("VK", "video"),
    # Musik
    "soundcloud.com": ("SoundCloud", "musik"),
    "bandcamp.com": ("Bandcamp", "musik"),
    "mixcloud.com": ("Mixcloud", "musik"),
    "audius.co": ("Audius", "musik"),
    # Mediatheken
    "ardmediathek.de": ("ARD", "mediathek"),
    "ard.de": ("ARD", "mediathek"),
    "zdf.de": ("ZDF", "mediathek"),
    "arte.tv": ("Arte", "mediathek"),
    "3sat.de": ("3sat", "mediathek"),
    "servustv.com": ("ServusTV", "mediathek"),
    "orf.at": ("ORF", "mediathek"),
    "srf.ch": ("SRF", "mediathek"),
    "br.de": ("BR", "mediathek"),
    "ndr.de": ("NDR", "mediathek"),
    "wdr.de": ("WDR", "mediathek"),
    "mdr.de": ("MDR", "mediathek"),
    "zdf-tivi.de": ("ZDF", "mediathek"),
    # Soziale Netze
    "tiktok.com": ("TikTok", "social"),
    "instagram.com": ("Instagram", "social"),
    "twitter.com": ("X", "social"),
    "x.com": ("X", "social"),
    "facebook.com": ("Facebook", "social"),
    "fb.watch": ("Facebook", "social"),
    "reddit.com": ("Reddit", "social"),
    "redd.it": ("Reddit", "social"),
    "threads.net": ("Threads", "social"),
    "bsky.app": ("Bluesky", "social"),
    # Erwachsenenseiten -- eigene Rubrik, damit sie sich im Verlauf
    # zusammen ein- und ausblenden lassen.
    "pornhub.com": ("Pornhub", "erwachsen"),
    "xvideos.com": ("XVideos", "erwachsen"),
    "xhamster.com": ("xHamster", "erwachsen"),
    "xnxx.com": ("XNXX", "erwachsen"),
    "youporn.com": ("YouPorn", "erwachsen"),
    "redtube.com": ("RedTube", "erwachsen"),
    "spankbang.com": ("SpankBang", "erwachsen"),
    "tube8.com": ("Tube8", "erwachsen"),
    "eporner.com": ("Eporner", "erwachsen"),
    "chaturbate.com": ("Chaturbate", "erwachsen"),
    "stripchat.com": ("Stripchat", "erwachsen"),
    "onlyfans.com": ("OnlyFans", "erwachsen"),
    "fansly.com": ("Fansly", "erwachsen"),
    "beeg.com": ("Beeg", "erwachsen"),
    "motherless.com": ("Motherless", "erwachsen"),
}
# Anzeigetexte der Rubriken (Reihenfolge = Reihenfolge in der WebGUI).
RUBRIKEN = (
    ("youtube", "Youtube"),
    ("musik", "Musik"),
    ("video", "Video-Portale"),
    ("mediathek", "Mediatheken"),
    ("social", "Soziale Netze"),
    ("erwachsen", "Erwachsene"),
    ("suche", "Suchanfragen"),
    ("sonstiges", "Sonstiges"),
)
RUBRIK_SCHLUESSEL = frozenset(k for k, _ in RUBRIKEN)

# Subdomains, die nur eine Darstellungsform sind und keinen eigenen Ordner
# verdienen. Alles andere bleibt stehen: news.example.com ist etwas anderes
# als example.com.
_FUELLSEL = ("www.", "m.", "mobile.", "de.", "en.")
# Namen, die Windows/SMB nicht als Ordner zulaesst.
_RESERVIERT = {"CON", "PRN", "AUX", "NUL",
               *(f"COM{i}" for i in range(1, 10)),
               *(f"LPT{i}" for i in range(1, 10))}
# Verboten sind die Zeichen, die Windows/SMB nicht im Namen zulaesst, dazu
# $ ~ %: yt-dlp schickt paths.home durch expand_path und wuerde daraus sonst
# Umgebungsvariablen oder das Heimatverzeichnis machen.
_UNZULAESSIG = re.compile(r'[<>:"/\\|?*$~%\x00-\x1f]')
# Ein Schema, dem KEIN // folgt und dessen Rest nicht mit Ziffer oder / beginnt:
# so sehen die Suchpraefixe von yt-dlp aus (ytsearch5:, scsearch:, prxseries:).
# "localhost:8000/x" faellt nicht darunter, weil danach eine Ziffer kommt.
_SUCHANFRAGE = re.compile(r"^[a-z][a-z0-9+.-]*:(?![0-9/])", re.I)
_ECHTE_SCHEMATA = ("http:", "https:", "ftp:", "ftps:", "file:", "rtmp:", "rtsp:")
_MAX_TIEFE = 8


def _sicherer_name(roh, ersatz: str = "Sonstige") -> str:
    """Einen einzelnen Ordnernamen auf allen Zielsystemen brauchbar machen.

    Das Ziel liegt oft auf einem SMB-Share. Dort sind mehr Zeichen verboten als
    unter Linux, und ein Name darf weder auf einen Punkt noch auf ein Leerzeichen
    enden -- sonst legt Windows ihn gar nicht erst an.
    """
    name = unicodedata.normalize("NFC", str(roh or ""))
    # Kategorie Cf sind unsichtbare Steuerzeichen -- das RTL-Override U+202E
    # dreht die Anzeige eines Ordnernamens im Dateimanager schlicht um.
    name = "".join(c for c in name if unicodedata.category(c) != "Cf")
    name = _UNZULAESSIG.sub("", name).strip(" .")
    if name.upper() in _RESERVIERT:
        name = "_" + name
    return name[:60].strip(" .") or ersatz


def _quelle_und_rubrik(url: str):
    """(Ordnername, Rubrik) fuer die Herkunft eines Downloads.

    Bewusst aus der URL abgeleitet und nicht aus yt-dlps extractor_key: Der
    Zielpfad muss VOR dem Start feststehen, und der Extraktor meldet fuer
    Playlists und Kanaele einen anderen Namen als fuer das einzelne Video
    ("YoutubeTab" statt "Youtube") -- dasselbe Video laege sonst je nach
    Aufruf in zwei Ordnern.

    Bekannte Plattformen bekommen einen sprechenden Namen und fassen ihre
    Schreibweisen zusammen (youtu.be, m.youtube.com und www.youtube.com sind
    ein Ordner). Alles Uebrige behaelt seinen Host, nur ohne die reinen
    Darstellungs-Subdomains.
    """
    roh = str(url or "").strip()
    if not roh:
        return "Sonstige", "sonstiges"
    if not roh.lower().startswith(_ECHTE_SCHEMATA) and _SUCHANFRAGE.match(roh):
        return "Suche", "suche"        # "ytsearch5:katzen" hat gar keinen Host
    if "://" not in roh:               # aus der Zwischenablage ohne Schema
        roh = "https://" + roh
    try:
        host = (urlsplit(roh).hostname or "").strip(".")
    except ValueError:                 # z.B. kaputte IPv6-Klammern
        return "Sonstige", "sonstiges"
    if not host:
        return "Sonstige", "sonstiges"
    try:                               # nackte IP: normiert, damit ::1 und
        adresse = ipaddress.ip_address(host)   # 0:0:0:0:0:0:0:1 gleich heissen
        return _sicherer_name(str(adresse).replace(":", "-")), "sonstiges"
    except ValueError:
        pass
    if "xn--" in host:                 # Punycode lesbar machen
        try:
            host = host.encode("ascii").decode("idna")
        except (UnicodeError, UnicodeDecodeError):
            pass
    teile = host.split(".")
    for i in range(len(teile) - 1):    # laengste Uebereinstimmung zuerst
        bekannt = _QUELLEN.get(".".join(teile[i:]))
        if bekannt:
            return bekannt
    kurz = host
    for f in _FUELLSEL:                # nur die reinen Darstellungsformen weg
        if kurz.startswith(f) and len(kurz) > len(f) + 1:
            kurz = kurz[len(f):]
            break
    return _sicherer_name(kurz), "sonstiges"


# ── Zulaessige Adressen ─────────────────────────────────────────────────────
_GESPERRTE_SCHEMATA = ("ftp:", "ftps:", "file:", "rtmp:", "rtsp:", "data:",
                       "javascript:")
_NUR_ZIFFERN_HOST = re.compile(r"^[0-9][0-9a-fx.]*$", re.I)


def _adresse_aus_host(host: str):
    """IP-Adresse aus einem Hostnamen, so wie der Resolver ihn lesen wuerde.

    ipaddress kennt nur die Punktschreibweise; die C-Bibliothek nimmt auch
    "2130706433" oder "0x7f.1" als 127.0.0.1. Wer die Sperre umgehen will,
    probiert genau das -- deshalb dieselbe Lesart wie inet_aton.
    """
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if _NUR_ZIFFERN_HOST.match(host):
        try:
            return ipaddress.ip_address(
                struct.unpack("!I", socket.inet_aton(host))[0])
        except (OSError, ValueError, struct.error):
            pass
    return None


def _ip_privat(adresse) -> bool:
    if adresse.version == 6 and adresse.ipv4_mapped is not None:
        adresse = adresse.ipv4_mapped         # ::ffff:127.0.0.1
    return bool(adresse.is_private or adresse.is_loopback
                or adresse.is_link_local or adresse.is_unspecified
                or adresse.is_multicast or adresse.is_reserved)


def url_pruefen(url: str) -> str:
    """Grund, warum diese Adresse NICHT geladen werden darf -- oder "".

    Erlaubt sind http(s)-Adressen (auch ohne Schema, das ergaenzt yt-dlp)
    und die Suchpraefixe von yt-dlp (ytsearch5:katzen). Abgelehnt:
    - andere Schemata (file:, ftp:, rtsp: ...) -- yt-dlp liefe damit
      gegen das Dateisystem oder fremde Protokolle,
    - Ziele im eigenen Netz oder auf dem Rechner selbst (10/8, 172.16/12,
      192.168/16, 127/8, 169.254/16, ::1, fc00::/7, localhost): der
      Container haengt am Host-Dateisystem, damit soll niemand interne
      Dienste abgrasen. Geprueft wird die Adresse, wie sie in der URL steht;
      Hostnamen werden nicht aufgeloest (kein Netz noetig, kein Warten).
    DOWNLOAD_ALLOW_PRIVATE=1 in der .env hebt nur den zweiten Punkt auf.
    """
    roh = str(url or "").strip()
    if not roh:
        return "Keine Adresse angegeben."
    klein = roh.lower()
    if klein.startswith(_GESPERRTE_SCHEMATA):
        return "Nur http- und https-Adressen sind erlaubt."
    if "://" not in roh:
        if _SUCHANFRAGE.match(roh):
            return ""                        # ytsearch5:... hat keinen Host
        roh = "https://" + roh
    try:
        teile = urlsplit(roh)
        host = (teile.hostname or "").strip(".").lower()
    except ValueError:
        return "Die Adresse ist nicht lesbar."
    if teile.scheme.lower() not in ("http", "https"):
        return "Nur http- und https-Adressen sind erlaubt."
    if not host:
        return "Die Adresse enthaelt keinen Hostnamen."
    if DOWNLOAD_ALLOW_PRIVATE:
        return ""
    if host == "localhost" or host.endswith(".localhost"):
        return ("Adressen im eigenen Netz oder auf diesem Rechner sind nicht "
                "erlaubt (localhost).")
    adresse = _adresse_aus_host(host)
    if adresse is not None and _ip_privat(adresse):
        return ("Adressen im eigenen Netz oder auf diesem Rechner sind nicht "
                f"erlaubt ({adresse}).")
    return ""


def _source_name(url: str) -> str:
    """Nur der Ordnername -- siehe _quelle_und_rubrik."""
    return _quelle_und_rubrik(url)[0]


def _rubrik_fuer(url: str) -> str:
    """Nur die Rubrik -- siehe _quelle_und_rubrik."""
    return _quelle_und_rubrik(url)[1]


def _clean_subdir(roh) -> str:
    """Einen Unterpfad auf harmlose Segmente eindampfen.

    Der Wert steht in den overrides eines Jobs und stammt bei einer Wiederaufnahme
    aus dem Verlauf. Ein Segment mit Traversal-Absicht laesst den ganzen Pfad
    verfallen; dann landet der Download flach im Zielordner, statt dass
    irgendwo ausserhalb davon geschrieben wird.
    """
    segmente = []
    for teil in re.split(r"[\\/]+", str(roh or "")):
        if not teil or teil == ".":            # "a//b" und "./a" sind harmlos
            continue
        if not teil.strip(" ."):               # "..", "...", "  ..  "
            return ""
        segmente.append(_sicherer_name(teil, ""))
    return "/".join([t for t in segmente if t][:_MAX_TIEFE])


def _subdir_for(url: str, mode: str) -> str:
    """Quelle / Art / Jahr / Monat -- das Datum ist der Zeitpunkt des Downloads."""
    art = "audio" if mode == "audio" else "video"
    jetzt = time.localtime()
    return "/".join((_source_name(url), art,
                     time.strftime("%Y", jetzt), time.strftime("%m", jetzt)))


# ── Altbestand nachtraeglich einsortieren ───────────────────────────────────
# Endungen, die als Tonspur zaehlen -- alles andere gilt als Video.
_AUDIO_EXT = {".mp3", ".m4a", ".opus", ".flac", ".wav", ".aac", ".ogg",
              ".oga", ".wma", ".alac", ".aiff"}


def _art_fuer(pfad: str) -> str:
    return "audio" if os.path.splitext(pfad)[1].lower() in _AUDIO_EXT else "video"


def _liegt_schon_richtig(ordner: Path, quelle: str, art: str) -> bool:
    """Endet der Pfad bereits auf <Quelle>/<Art>/<Jahr>/<Monat>?

    Zweite Sicherung neben dem subdir-Vermerk im Verlauf: ohne sie wuerde ein
    zweiter Durchlauf das Schema ein weiteres Mal anhaengen.
    """
    teile = ordner.parts
    if len(teile) < 4:
        return False
    q, a, jahr, monat = teile[-4:]
    return (q == quelle and a == art and len(jahr) == 4 and jahr.isdigit()
            and len(monat) == 2 and monat.isdigit())


def organize_plan(limit: int = 5000) -> dict:
    """Trockenlauf: welche fertige Datei wuerde wohin wandern?

    Jahr und Monat stammen aus dem Zeitpunkt des Downloads, NICHT aus dem
    heutigen Datum -- sonst landete der gesamte Altbestand im laufenden Monat
    und die Ordner waeren als Zeitachse wertlos.
    """
    plan, uebersprungen, fehlend = [], 0, 0
    for rec in history("done", limit):
        subdir_bekannt = bool((rec.get("opts") or {}).get("subdir"))
        for datei in rec.get("files") or []:
            pfad = Path(datei)
            if not pfad.is_file():
                fehlend += 1
                continue
            quelle = _source_name(rec.get("url", ""))
            art = _art_fuer(datei)
            if subdir_bekannt or _liegt_schon_richtig(pfad.parent, quelle, art):
                uebersprungen += 1
                continue
            wann = time.localtime(int(rec.get("finished") or time.time()))
            ziel_ordner = (pfad.parent / _sicherer_name(quelle) / art
                           / time.strftime("%Y", wann)
                           / time.strftime("%m", wann))
            plan.append({
                "jid": rec["id"],
                "titel": rec.get("title") or pfad.name,
                "von": str(pfad),
                "nach": str(ziel_ordner / pfad.name),
                "quelle": quelle,
                "rubrik": _rubrik_fuer(rec.get("url", "")),
                "groesse": pfad.stat().st_size,
                "kollision": (ziel_ordner / pfad.name).exists(),
            })
    return {"moves": plan, "uebersprungen": uebersprungen, "fehlend": fehlend,
            "gesamt_bytes": sum(m["groesse"] for m in plan)}


def organize_run(nur_jids=None) -> dict:
    """Den Plan ausfuehren und den Verlauf mitziehen.

    Verschoben wird nur, wenn am Ziel nichts gleichnamiges liegt: eine fremde
    Datei zu ueberschreiben waere ein stiller Datenverlust. Solche Faelle
    kommen als "uebersprungen" zurueck und bleiben, wo sie sind.
    """
    plan = organize_plan()["moves"]
    if nur_jids:
        erlaubt = set(nur_jids)
        plan = [m for m in plan if m["jid"] in erlaubt]
    verschoben, uebersprungen, fehler = 0, 0, []
    neue_dateien = {}          # jid -> {alt: neu}
    for m in plan:
        quelle_pfad, ziel_pfad = Path(m["von"]), Path(m["nach"])
        try:
            if ziel_pfad.exists():
                uebersprungen += 1
                continue
            ziel_pfad.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.replace(quelle_pfad, ziel_pfad)      # gleiches Dateisystem
            except OSError:
                shutil.move(str(quelle_pfad), str(ziel_pfad))
            neue_dateien.setdefault(m["jid"], {})[m["von"]] = m["nach"]
            verschoben += 1
        except OSError as e:
            fehler.append(f"{quelle_pfad.name}: {e}")
    for jid, zuordnung in neue_dateien.items():
        _verlauf_umziehen(jid, zuordnung)
    return {"verschoben": verschoben, "uebersprungen": uebersprungen,
            "fehler": fehler}


def _verlauf_umziehen(jid: str, zuordnung: dict):
    """Nach dem Verschieben die Pfade im Verlauf nachziehen.

    Ohne das zeigten Ansehen, Herunterladen und Loeschen ins Leere -- die
    Bibliothek arbeitet ausschliesslich mit den gespeicherten Pfaden.
    """
    rec = history_one(jid)
    if not rec:
        return
    dateien = [zuordnung.get(f, f) for f in rec.get("files") or []]
    opts = dict(rec.get("opts") or {})
    neuer_ordner = os.path.dirname(dateien[0]) if dateien else rec.get("dest", "")
    basis = opts.get("dest") or rec.get("dest") or ""
    if basis and neuer_ordner.startswith(basis.rstrip(os.sep) + os.sep):
        opts["subdir"] = os.path.relpath(neuer_ordner, basis).replace(os.sep, "/")
    with _db_lock:
        conn = _db()
        conn.execute("UPDATE history SET files=?, dest=?, opts=? WHERE id=?",
                     (json.dumps(dateien, ensure_ascii=False), neuer_ordner,
                      json.dumps(opts, ensure_ascii=False), jid))
        conn.commit()
        conn.close()


# ── Browser-Tarnung (Impersonation) ─────────────────────────────────────────
# PornHub und Cloudflare-geschuetzte Portale sperren die TLS-/HTTP-Signatur
# von urllib mit "HTTP Error 403: Forbidden", obwohl dieselbe Seite im Browser
# laeuft. yt-dlp kann sich ueber curl_cffi als Browser ausgeben; dafuer MUSS
# curl_cffi installiert sein (requirements.txt) -- ohne das Paket ignoriert
# yt-dlp selbst die Tarnung, die der Extraktor anfordert, und die Seite
# scheitert. Genau das war der PornHub-Fehler vom 28.09.2026.
TARNUNG_ZIEL = "chrome"
_SPERRE_MUSTER = re.compile(r"\b403\b|forbidden|access denied|cloudflare|just a moment",
                            re.IGNORECASE)


def _tarnung_verfuegbar() -> bool:
    """curl_cffi vorhanden? Nur dann akzeptiert yt-dlp ein impersonate-Ziel."""
    return getattr(yt_dlp.dependencies, "curl_cffi", None) is not None


def _tarnung_modus(s) -> str:
    modus = str(s.get("browser_tarnung") or "auto").lower()
    return modus if modus in TARNUNG_MODI else "auto"


def _tarnung_setzen(opts: dict):
    opts["impersonate"] = ImpersonateTarget.from_str(TARNUNG_ZIEL)


def _protokoll(job, level: str, msg: str):
    job["log"].append(f"{time.strftime('%H:%M:%S')} {level} {msg}")


def _sieht_nach_sperre_aus(job, fehler) -> bool:
    text = f"{job.get('last_error', '')} {fehler}"
    return bool(_SPERRE_MUSTER.search(text))


def _tarnung_nachlegen(job, opts: dict, s, fehler) -> bool:
    """Nach einem Fehlschlag entscheiden, ob ein getarnter zweiter Lauf folgt.

    True = opts wurden umgestellt, der Aufrufer wiederholt den Lauf. Greift nur
    im Modus "auto", nur bei einer Sperre (403/Forbidden/Cloudflare) und nur,
    wenn der erste Lauf noch nicht getarnt war.
    """
    if _tarnung_modus(s) != "auto" or "impersonate" in opts:
        return False
    if not _sieht_nach_sperre_aus(job, fehler):
        return False
    if not _tarnung_verfuegbar():
        _protokoll(job, "WARN", "Seite verweigert den Zugriff (403); Browser-Tarnung "
                   "nicht moeglich, weil curl_cffi im Container fehlt.")
        return False
    _protokoll(job, "info", f"Seite verweigert den Zugriff (403) -- zweiter Versuch "
               f"mit Browser-Tarnung ({TARNUNG_ZIEL}).")
    job["last_error"] = ""
    _tarnung_setzen(opts)
    return True


# ── yt-dlp Options-Bau ──────────────────────────────────────────────────────
# yt-dlp bettet Titelbilder nur in diese Endungen ein (EmbedThumbnailPP). Bei
# jeder anderen wirft es -- und bricht damit die Nachbearbeitung ab, BEVOR die
# fertige Datei aus dem Zwischenordner ans Ziel wandert. Am 30.09.2026 lag so
# eine WAV unerreichbar in config/tmp, obwohl der Verlauf "fertig" meldete.
_TITELBILD_ENDUNGEN = frozenset(("mp3", "mkv", "mka", "m4a", "mp4", "m4v", "mov",
                                 "ogg", "opus", "flac"))
# Endung je Zielcodec von FFmpegExtractAudio, wo sie vom Codecnamen abweicht.
_AUDIO_ENDUNG = {"vorbis": "ogg", "alac": "m4a"}


def _zielendung(mode: str, audio_fmt, container: str) -> str:
    """Endung der fertigen Datei -- "" heisst: die legt erst yt-dlp fest.

    Fuers Titelbild ist beides unkritisch: "best" behaelt den Codec der Quelle
    und landet dabei immer in mp3/m4a/opus/ogg/flac, und bei "auto" macht
    yt-dlp aus einem zusammengefuegten webm selbst ein mkv, sobald ein
    Titelbild eingebettet werden soll. Den seltenen Rest (eine einzelne
    webm-Datei ohne Zusammenfuegen) faengt _aus_zwischenordner_holen auf.
    """
    if mode == "audio":
        fmt = str(audio_fmt or "best").lower()
        return "" if fmt == "best" else _AUDIO_ENDUNG.get(fmt, fmt)
    return "" if container == "auto" else container


def _build_opts(job, s) -> dict:
    o = job["overrides"]
    dest = o.get("dest") or s["default_dest"]

    mode = o.get("format_mode", s["format_mode"])
    if mode == "mp4":                       # Altwert aus Version 1
        mode = "best"
        o.setdefault("video_container", "mp4")
    max_q = str(o.get("max_quality", s["max_quality"]) or "0")
    container = str(o.get("video_container") or s["video_container"] or "auto").lower()
    if container not in VIDEO_CONTAINERS:
        container = "auto"

    # Ablage in Unterordnern: <Zielordner>/Quelle/Art/Jahr/Monat.
    #
    # Beide Haelften des Zielpfades werden beim ersten Lauf in den overrides
    # festgeschrieben und wandern von dort in den Verlauf. Nur so trifft eine
    # Wiederaufnahme denselben Ordner und findet ihre .part-Datei -- selbst
    # wenn inzwischen ein Monat vergangen ist oder der Standard-Zielordner
    # geaendert wurde. Nebenbei verhindert es, dass das Schema bei jedem
    # weiteren Versuch ein zweites Mal angehaengt wird.
    o.setdefault("dest", dest)

    roh = o.get("subdir")
    if roh is None:                      # neuer Job -> Schema anwenden
        sub = (_subdir_for(job["url"], mode)
               if s.get("organize_subdirs", True) else "")
        o["subdir"] = sub
    else:                                # aus dem Verlauf -- "" heisst: flach
        sub = _clean_subdir(roh)
    Path(dest).mkdir(parents=True, exist_ok=True)
    if sub:
        # Den Unterordner legt yt-dlp beim ersten Schreiben selbst an. Wuerde
        # er hier entstehen, bliebe nach jeder Fehleingabe ein leeres Geruest
        # im Zielordner zurueck.
        ziel = os.path.join(dest, *sub.split("/"))
        # Letzte Instanz: was auch immer vorne hineingeraten ist -- der
        # fertige Pfad muss unterhalb des Zielordners liegen.
        basis = os.path.realpath(dest)
        if os.path.realpath(ziel).startswith(basis + os.sep):
            dest = ziel
        else:
            o["subdir"] = ""
    job["dest"] = dest

    opts = {
        "outtmpl": o.get("output_template") or s["output_template"],
        "paths": {"home": dest},
        "noprogress": True,
        "quiet": True,
        "no_warnings": False,
        # Nur Download-Fehler einzelner Playlist-Eintraege ueberspringen.
        # Bei "True" wuerde yt-dlp AUCH Extraktionsfehler schlucken und
        # kommentarlos None liefern -- dann steht in der GUI eine geratene
        # Meldung statt der echten Ursache.
        "ignoreerrors": "only_download",
        "restrictfilenames": bool(s["restrict_filenames"]),
        "concurrent_fragment_downloads": max(1, int(s["concurrent_fragments"])),
        "progress_hooks": [lambda d: _hook(job, d)],
        "postprocessor_hooks": [lambda d: _pp_hook(job, d)],
        "postprocessors": [],
        "windowsfilenames": False,
        "logger": _JobLogger(job),
        # Teil-Downloads behalten -> abgebrochene Jobs lassen sich fortsetzen.
        "continuedl": True,
        "nopart": False,
        "retries": 10,
        "fragment_retries": 10,
        "file_access_retries": 5,
        # Ohne Zeitlimit blieb ein Lesevorgang an einer eingeschlafenen
        # Verbindung ewig haengen -- der Worker war dann fuer immer weg.
        "socket_timeout": 30,
        # Wird vor jedem (Playlist-)Eintrag geprueft: so greift ein Abbruch
        # auch zwischen zwei Videos und nicht erst beim naechsten Chunk.
        "match_filter": lambda info, *, incomplete=False: _cancel_guard(job),
    }

    if s["local_temp"]:
        # Fragmente und ffmpeg-Zwischendateien lokal halten, erst das fertige
        # Ergebnis aufs Ziel schreiben. Bei SMB-/NAS-Zielen spart das den
        # Grossteil der Netzwerk-I/O (1x kopieren statt 3x uebers Netz).
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        opts["paths"]["temp"] = str(TEMP_DIR)

    if s["use_cookies"] and COOKIES_FILE.exists():
        opts["cookiefile"] = str(COOKIES_FILE)

    # Hinterlegte Zugangsdaten fuer genau diese Seite. Sie stehen bewusst NICHT
    # in den overrides -- sonst landeten sie ueber den Verlauf in der Datenbank.
    zugang = login_for(job["url"])
    if zugang.get("username") or zugang.get("password"):
        opts["username"] = zugang.get("username", "")
        opts["password"] = zugang.get("password", "")
    if zugang.get("videopassword"):
        opts["videopassword"] = zugang["videopassword"]

    if s["rate_limit"]:
        opts["ratelimit"] = _parse_rate(s["rate_limit"])
    if o.get("playlist_items") or s["playlist_items"]:
        opts["playlist_items"] = o.get("playlist_items") or s["playlist_items"]

    # Ganze Liste oder nur der verlinkte Titel? Der Wert wird in BEIDE
    # Richtungen gesetzt: ohne ihn entscheidet yt-dlp je nach Portal selbst,
    # und dieselbe URL brachte mal ein Video, mal hundert.
    pl_modus = str(o.get("playlist_mode") or s.get("playlist_mode")
                   or "playlist").lower()
    if pl_modus not in PLAYLIST_MODES:
        pl_modus = "playlist"
    opts["noplaylist"] = (pl_modus == "single")

    # ── Format-Auswahl ──────────────────────────────────────────────────────
    if mode == "audio":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"].append({
            "key": "FFmpegExtractAudio",
            "preferredcodec": o.get("audio_format", s["audio_format"]),
            "preferredquality": str(s["audio_quality"]),
        })
    elif mode == "custom" and (o.get("custom_format") or s["custom_format"]):
        opts["format"] = o.get("custom_format") or s["custom_format"]
    else:
        cap = f"[height<=?{max_q}]" if max_q not in ("0", "", None) else ""
        if container == "mp4":
            # Fuer MP4 zuerst die nativ passenden Spuren versuchen -- dann
            # muss ffmpeg spaeter gar nichts mehr umpacken.
            opts["format"] = (f"bestvideo{cap}[ext=mp4]+bestaudio[ext=m4a]/"
                              f"bestvideo{cap}+bestaudio/best{cap}")
        else:
            opts["format"] = f"bestvideo{cap}+bestaudio/best{cap}"

    # ── Zielcontainer (verlustfrei) ─────────────────────────────────────────
    if mode != "audio" and container != "auto":
        # merge_output_format greift, wenn Video und Audio getrennt geladen und
        # zusammengefuegt werden. Laedt yt-dlp eine fertige Einzeldatei, packt
        # der Remuxer sie nachtraeglich um -- ohne neu zu kodieren, also in
        # Sekunden und ohne Qualitaetsverlust.
        opts["merge_output_format"] = container
        opts["postprocessors"].append({
            "key": "FFmpegVideoRemuxer", "preferedformat": container})

    # ── Untertitel ──────────────────────────────────────────────────────────
    if s["write_subs"] or s["embed_subs"]:
        opts["writesubtitles"] = True
        opts["subtitleslangs"] = [x.strip() for x in s["sub_langs"].split(",") if x.strip()]
        if s["embed_subs"]:
            opts["postprocessors"].append({"key": "FFmpegEmbedSubtitle"})

    # ── SponsorBlock (Werbung/Intro entfernen) ──────────────────────────────
    if s["sponsorblock"]:
        cats = ["sponsor", "intro", "outro", "selfpromo"]
        opts["postprocessors"].append({"key": "SponsorBlock", "categories": cats})
        opts["postprocessors"].append({
            "key": "ModifyChapters", "remove_sponsor_segments": cats})

    # Metadaten/Thumbnail zuletzt -- so landen sie im FINALEN Container.
    if s["embed_metadata"]:
        opts["postprocessors"].append({"key": "FFmpegMetadata", "add_metadata": True})
    if s["embed_thumbnail"] and mode != "custom":
        endung = _zielendung(mode, o.get("audio_format", s["audio_format"]), container)
        if endung and endung not in _TITELBILD_ENDUNGEN:
            _protokoll(job, "info", f"Titelbild wird nicht eingebettet -- "
                                    f"{endung.upper()} kann keins tragen.")
        else:
            opts["writethumbnail"] = True
            opts["postprocessors"].append({"key": "EmbedThumbnail"})

    # ── Browser-Tarnung ─────────────────────────────────────────────────────
    # "immer" tarnt von Anfang an; "auto" legt erst nach einem 403 nach
    # (_tarnung_nachlegen), damit der Normalfall so schnell bleibt wie bisher.
    if _tarnung_modus(s) == "immer":
        if _tarnung_verfuegbar():
            _tarnung_setzen(opts)
        else:
            _protokoll(job, "WARN", "Browser-Tarnung 'immer' eingestellt, aber "
                       "curl_cffi fehlt im Container -- Lauf ohne Tarnung.")

    return opts


def _parse_rate(v: str):
    v = str(v).strip().upper()
    try:
        if v.endswith("K"):
            return int(float(v[:-1]) * 1024)
        if v.endswith("M"):
            return int(float(v[:-1]) * 1024 * 1024)
        if v.endswith("G"):
            return int(float(v[:-1]) * 1024 * 1024 * 1024)
        return int(v)
    except ValueError:
        return None


# ── Progress-Hooks ──────────────────────────────────────────────────────────
def _cancel_guard(job):
    """DownloadCancelled ist die einzige Exception, die yt-dlp NICHT schluckt.

    Eine eigene Exception-Klasse wurde vom Fehler-Handling abgefangen und in
    ein stilles None umgewandelt -- der Job landete dann faelschlich als
    "Fehler" statt als "abgebrochen" im Verlauf.
    """
    if job.get("cancel"):
        raise DownloadCancelled("Vom Nutzer abgebrochen.")
    return None


def _smooth_eta(job, eta):
    """Gleitender Mittelwert -- die Roh-ETA von yt-dlp springt sonst wild."""
    if eta is None:
        job["eta_smooth"] = None
        return
    prev = job.get("eta_smooth")
    job["eta_smooth"] = int(eta) if prev is None else int(prev * 0.7 + eta * 0.3)


def _hook(job, d):
    _cancel_guard(job)
    st = d.get("status")
    if st == "downloading":
        was = job["status"]
        job["status"] = "downloading"
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        done = d.get("downloaded_bytes") or 0
        job["total"] = total
        job["downloaded"] = done
        job["progress"] = round(done / total * 100, 1) if total else 0.0
        job["speed"] = d.get("speed") or 0
        job["eta"] = d.get("eta")
        _smooth_eta(job, d.get("eta"))
        info = d.get("info_dict") or {}
        if info.get("title"):
            job["title"] = info["title"]
        if info.get("duration"):
            job["duration"] = int(info["duration"])
        if info.get("playlist_index"):
            job["playlist_index"] = info.get("playlist_index")
            job["playlist_count"] = info.get("n_entries")
        fn = d.get("filename")
        if fn:
            job["filename"] = os.path.basename(fn)
        _publish_job(job, force=was != "downloading")
    elif st == "finished":
        job["progress"] = 100.0
        job["status"] = "processing"
        job["speed"] = 0
        job["eta"] = job["eta_smooth"] = None
        fp = (d.get("info_dict") or {}).get("filepath") or d.get("filename")
        if fp:
            job.setdefault("seen_files", set()).add(fp)
        _publish_job(job)
    elif st == "error":
        job.setdefault("log", deque(maxlen=120)).append("Download-Fehler.")


def _pp_hook(job, d):
    _cancel_guard(job)
    if d.get("status") == "started":
        job["status"] = "processing"
        job["filename"] = os.path.basename(
            (d.get("info_dict") or {}).get("filepath") or job.get("filename", ""))
        _publish_job(job)
    elif d.get("status") == "finished":
        # Nach Remux/Audio-Extraktion aendert sich die Endung -> neu merken.
        fp = (d.get("info_dict") or {}).get("filepath")
        if fp:
            job.setdefault("seen_files", set()).add(fp)


def _collect_files(job, info) -> list:
    """Alle tatsaechlich geschriebenen Zieldateien einsammeln."""
    found = list(job.get("seen_files") or [])

    def walk(d):
        if not isinstance(d, dict):
            return
        for rd in (d.get("requested_downloads") or []):
            if isinstance(rd, dict) and rd.get("filepath"):
                found.append(rd["filepath"])
        if d.get("filepath"):
            found.append(d["filepath"])
        for e in (d.get("entries") or []):
            walk(e)

    walk(info)
    seen, out = set(), []
    for f in found:
        if f and f not in seen and os.path.isfile(f):
            seen.add(f)
            out.append(f)
    return out


# ── Zwischenordner (local_temp) ─────────────────────────────────────────────
# Bilder, die yt-dlp nur zum Einbetten in den Zwischenordner legt.
_BILD_ENDUNGEN = (".webp", ".png", ".jpg", ".jpeg")


def _im_zwischenordner(pfad: str) -> bool:
    return os.path.realpath(pfad).startswith(os.path.realpath(TEMP_DIR) + os.sep)


def _freier_name(pfad: str) -> str:
    """Nie ueberschreiben: aus "Titel.wav" wird "Titel (2).wav", wenn belegt."""
    if not os.path.exists(pfad):
        return pfad
    stamm, endung = os.path.splitext(pfad)
    n = 2
    while os.path.exists(f"{stamm} ({n}){endung}"):
        n += 1
    return f"{stamm} ({n}){endung}"


def _aus_zwischenordner_holen(dateien: list, ziel: str):
    """Was noch im Zwischenordner liegt, dorthin bringen, wo yt-dlp es ablegt.

    yt-dlp verschiebt erst als allerletzten Schritt der Nachbearbeitung. Bricht
    ein Schritt davor ab, bleibt die fertige Datei in config/tmp -- ausserhalb
    der freigegebenen Ordner, also weder abspielbar noch herunterladbar.
    Liefert die neue Dateiliste und wie viele Dateien umgezogen sind.
    """
    basis = os.path.realpath(TEMP_DIR)
    neu, umgezogen = [], 0
    for f in dateien:
        if not ziel or not _im_zwischenordner(f) or not os.path.isfile(f):
            neu.append(f)
            continue
        nach = _freier_name(os.path.join(ziel, os.path.relpath(os.path.realpath(f), basis)))
        os.makedirs(os.path.dirname(nach), exist_ok=True)
        shutil.move(f, nach)        # config/ und Ziel liegen meist auf verschiedenen Platten
        stamm = os.path.splitext(f)[0]
        for endung in _BILD_ENDUNGEN:
            rest = stamm + endung
            if rest not in dateien:
                try:
                    os.remove(rest)
                except OSError:
                    pass
        neu.append(nach)
        umgezogen += 1
    return neu, umgezogen


def _zwischenordner_reste_retten():
    """Fertige Verlaufseintraege reparieren, deren Datei noch in config/tmp liegt.

    So endeten bis 2.5.2 Downloads, deren Nachbearbeitung abbrach (WAV mit
    Titelbild): "fertig" im Verlauf, die Datei aber im Zwischenordner. Nur
    fertige Eintraege -- die Reste abgebrochener braucht "Fortsetzen".
    """
    with _db_lock:
        conn = _db()
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM history WHERE status = 'done' AND files LIKE ?",
            (f"%{TEMP_DIR}{os.sep}%",))]
        conn.close()
    for jid in ids:
        rec = history_one(jid) or {}
        dateien = rec.get("files") or []
        try:
            neu, umgezogen = _aus_zwischenordner_holen(dateien, rec.get("dest") or "")
        except OSError:
            continue                    # naechster Start versucht es erneut
        if not umgezogen:
            continue
        _verlauf_umziehen(jid, dict(zip(dateien, neu)))
        with _db_lock:
            conn = _db()
            conn.execute("UPDATE history SET log = COALESCE(log, '') || ? WHERE id = ?",
                         (f"\n{time.strftime('%H:%M:%S')} WARN Nachtraeglich aus dem "
                          "Zwischenordner ans Ziel verschoben (Nachbearbeitung war "
                          "abgebrochen).", jid))
            conn.commit()
            conn.close()


# ── Worker ──────────────────────────────────────────────────────────────────
def _zeitlimit_sekunden(s) -> int:
    """Zeitlimit je Auftrag aus den Einstellungen; 0 = keins."""
    try:
        return max(0, int(s.get("job_timeout_h", 6))) * 3600
    except (TypeError, ValueError):
        return 6 * 3600


def _dauer_text(sekunden: int) -> str:
    if sekunden >= 3600 and sekunden % 3600 == 0:
        return f"{sekunden // 3600} h"
    if sekunden >= 60:
        return f"{sekunden // 60} min"
    return f"{sekunden} s"


def _waechter_starten(job, sekunden: int):
    """Nach Ablauf den Auftrag abbrechen -- ueber denselben Weg wie cancel().

    Der Timer setzt nur das cancel-Flag; die eigentliche Unterbrechung kommt
    vom naechsten Fortschritts-Callback (DownloadCancelled). Zusammen mit
    socket_timeout ist das zuverlaessig: ein haengender Socket faellt nach
    30 s auf yt-dlps Wiederholung zurueck, und die ruft den Hook.
    """
    if sekunden <= 0:
        return None

    def _ablauf():
        with _jobs_lock:
            if job["status"] not in ("downloading", "processing") or job.get("cancel"):
                return                        # schon fertig oder vom Nutzer abgebrochen
            job["cancel"] = True
            job["zeitlimit"] = True

    t = threading.Timer(sekunden, _ablauf)
    t.daemon = True
    t.start()
    return t


def _lauf(job, opts: dict):
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(job["url"], download=True)


def _run_job(jid):
    with _jobs_lock:
        job = _jobs.get(jid)
        if not job or job.get("cancel"):
            # Wurde in der Warteschlange abgebrochen -- bereits verbucht.
            # Pruefung und Statuswechsel muessen unter EINEM Lock passieren,
            # sonst ueberschreibt der Worker ein zeitgleiches cancel().
            return
        job["status"] = "downloading"
    s = get_settings()
    _publish_job(job)
    info = None
    limit = _zeitlimit_sekunden(s)
    waechter = _waechter_starten(job, limit)
    try:
        # Zweite Instanz der Adresspruefung, direkt vor dem Start: enqueue()
        # prueft bereits, aber der Worker soll auch dann nicht loslaufen,
        # wenn ein Auftrag auf anderem Weg in die Warteschlange geriet.
        grund = url_pruefen(job["url"])
        if grund:
            raise RuntimeError(grund)
        opts = _build_opts(job, s)
        try:
            info = _lauf(job, opts)
        except DownloadCancelled:
            raise
        except Exception as e:
            # Sperre (403)? Dann einmal getarnt nachlegen -- alles andere
            # bleibt ein Fehler wie bisher.
            if not _tarnung_nachlegen(job, opts, s, e):
                raise
            _cancel_guard(job)
            info = _lauf(job, opts)
        _cancel_guard(job)
        job["files"] = _collect_files(job, info)
        if not info or not job["files"]:
            raise RuntimeError(job.get("last_error") or
                               "Download fehlgeschlagen -- keine Datei erzeugt "
                               "(Seite nicht unterstuetzt, offline oder privat).")
        # Nachbearbeitung abgebrochen? Dann hat yt-dlp auch nicht mehr aus dem
        # Zwischenordner verschoben -- nachholen, statt "fertig" mit einer
        # Datei zu melden, an die weder Ansehen noch Laden herankommen.
        job["files"], umgezogen = _aus_zwischenordner_holen(job["files"],
                                                            job.get("dest") or "")
        if isinstance(info, dict):
            job["title"] = info.get("title") or job["title"]
            job["duration"] = int(info.get("duration") or job.get("duration") or 0)
        job["filename"] = os.path.basename(job["files"][0])
        job["filesize"] = sum(os.path.getsize(f) for f in job["files"]
                              if os.path.isfile(f))
        job["status"] = "done"
        job["progress"] = 100.0
        job["error"] = ""
        if umgezogen:
            _protokoll(job, "WARN", "Nachbearbeitung abgebrochen -- fertige Datei "
                                    "selbst aus dem Zwischenordner ans Ziel gelegt.")
            job["error"] = (f"Nachbearbeitung unvollstaendig "
                            f"({job.get('last_error') or 'ohne Meldung'}) -- "
                            "die Datei liegt trotzdem am Ziel.")[:500]
    except DownloadCancelled:
        if job.get("zeitlimit"):
            # Vom Waechter beendet: ein Fehler, kein Nutzerwunsch. Der
            # Teil-Download bleibt trotzdem fuer "Fortsetzen" liegen.
            job["status"] = "error"
            job["error"] = (f"Zeitlimit von {_dauer_text(limit)} ueberschritten -- "
                            "Download abgebrochen. Der Teil-Download bleibt "
                            "erhalten und laesst sich fortsetzen.")
        else:
            job["status"] = "canceled"
            job["error"] = ("Vom Nutzer abgebrochen. Teil-Download bleibt erhalten "
                            "und wird bei der Wiederaufnahme fortgesetzt.")
        job["files"] = _collect_files(job, info)
    except Exception as e:
        job["status"] = "error"
        job["error"] = (job.get("last_error") or str(e) or type(e).__name__)[:500]
        job["files"] = _collect_files(job, info)
    finally:
        if waechter is not None:
            waechter.cancel()
    job["speed"] = 0
    job["eta"] = job["eta_smooth"] = None
    job["ended"] = time.time()
    _record(job)
    _publish_job(job)
    _broadcast({"type": "history"})


def _worker_loop():
    while True:
        with _jobs_lock:
            if len(_workers) > _worker_target and threading.current_thread() in _workers:
                _workers.remove(threading.current_thread())
                return
        try:
            jid = _q.get(timeout=1.0)
        except queue.Empty:
            continue
        try:
            _run_job(jid)
        finally:
            _q.task_done()


def _ensure_workers():
    global _worker_target
    s = get_settings()
    _worker_target = max(1, int(s["concurrent_downloads"]))
    with _jobs_lock:
        while len(_workers) < _worker_target:
            t = threading.Thread(target=_worker_loop, daemon=True)
            _workers.append(t)
            t.start()


def start():
    global _example_started
    _ensure_workers()
    if not _example_started:
        _example_started = True
        threading.Thread(target=_example, daemon=True).start()
        # Im Hintergrund: eine grosse Datei auf eine andere Platte zu kopieren
        # soll den Start nicht aufhalten.
        threading.Thread(target=_zwischenordner_reste_retten, daemon=True).start()

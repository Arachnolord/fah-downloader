# ============================================================================
# Erdacht und programmiert von Fabian Hammer.
# (c) 2026 Fabian Hammer - Alle Rechte vorbehalten. Geistiges Eigentum.
# ============================================================================
"""Ansehen im Browser -- auch fuer Container, die kein Browser abspielt.

Das Problem: MKV ist der robusteste Container und darum die Standardwahl beim
Download, aber KEIN verbreiteter Browser spielt ihn ab. Ein <video>-Element
bekommt die Datei, laedt sie stumpf herunter und zeigt nie ein Bild -- der
Player "laedt sich dumm und daemlich".

Die Loesung ist ein Umpacken, keine Neukodierung: Bild und meistens auch Ton
werden Byte fuer Byte uebernommen (`-c copy`), nur die Huelle wechselt nach
MP4 bzw. WebM. Das dauert Sekunden statt Stunden und kostet keine Qualitaet.
Nur wenn die Tonspur in der Zielhuelle nicht erlaubt ist (AC3, DTS, TrueHD),
wird sie -- und ausschliesslich sie -- nach AAC bzw. Opus umgewandelt.

Das Ergebnis landet als Zwischendatei in CONFIG_DIR/playcache. Erst dadurch
laesst sich im Player spulen: ein direkt durchgereichter ffmpeg-Strom kann
keine Range-Anfragen beantworten. Der Zwischenspeicher raeumt sich selbst auf,
aelteste Datei zuerst.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from config import CONFIG_DIR

CACHE_DIR = CONFIG_DIR / "playcache"

# Huellen, die jeder verbreitete Browser oeffnet -- die gehen unveraendert raus.
DIREKT_CONTAINER = {".mp4", ".m4v", ".webm", ".ogv", ".ogg", ".mov",
                    ".mp3", ".m4a", ".opus", ".wav", ".flac", ".aac"}

# Bildspuren, die ein Browser darstellen kann, mit ihrer passenden Zielhuelle.
_VIDEO_ZIEL = {
    "h264": ".mp4", "avc1": ".mp4",
    "av1": ".mp4", "av01": ".mp4",
    "vp9": ".webm", "vp09": ".webm", "vp8": ".webm",
}
# Tonspuren, die in der jeweiligen Zielhuelle unveraendert bleiben duerfen.
_AUDIO_OK = {".mp4": {"aac", "mp3", "alac"}, ".m4a": {"aac", "alac"},
             ".webm": {"opus", "vorbis"}, ".opus": {"opus"}}
_AUDIO_ERSATZ = {".mp4": ("aac", "192k"), ".m4a": ("aac", "192k"),
                 ".webm": ("libopus", "160k"), ".opus": ("libopus", "160k")}
# ffmpeg leitet das Ausgabeformat sonst aus der Dateiendung ab -- und die
# Zwischendatei heisst waehrend der Arbeit ".teil". Ohne diese Angabe bricht
# der Lauf mit "use a standard extension" ab.
_FFMPEG_FORMAT = {".mp4": "mp4", ".m4a": "mp4", ".webm": "webm", ".opus": "opus"}

_laeuft = {}                 # Cache-Schluessel -> Fortschritts-Dikt
_lock = threading.Lock()

# Laenger darf ein Umpack-Lauf nicht dauern. Verlustfreies Umpacken schafft
# ein Vielfaches der Echtzeit; eine Stunde ist selbst fuer einen Spielfilm
# von einem langsamen NAS grosszuegig. Danach wird ffmpeg abgeschossen.
_UMPACK_ZEITLIMIT = 3600
# Zeilen von "-progress pipe:1" sehen so aus: out_time_ms=1234, speed=12x.
# Alles andere im gemeinsamen Ausgabestrom ist eine Fehlermeldung von ffmpeg.
_PROGRESS_ZEILE = re.compile(r"^[a-z_0-9.]+=")


def _ffmpeg_da() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def pruefe_datei(pfad: str) -> dict:
    """Spuren einer Datei ermitteln (leeres Dikt, wenn ffprobe nicht kann)."""
    if not _ffmpeg_da():
        return {}
    try:
        roh = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format",
             "-of", "json", pfad],
            capture_output=True, text=True, timeout=60)
        daten = json.loads(roh.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    video = audio = ""
    for s in daten.get("streams", []):
        art, name = s.get("codec_type"), (s.get("codec_name") or "").lower()
        if art == "video" and not video and not s.get("disposition", {}).get("attached_pic"):
            video = name
        elif art == "audio" and not audio:
            audio = name
    try:
        dauer = float(daten.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        dauer = 0.0
    return {"video": video, "audio": audio, "dauer": dauer}


def plan_fuer(pfad: str) -> dict:
    """Wie kommt diese Datei in den Browser?

    modus "direkt"    -- unveraendert ausliefern (wie bisher)
    modus "umpacken"  -- vorher in eine browsertaugliche Huelle bringen
    modus "nein"      -- ginge nur mit echter Neukodierung; ehrlich sagen
    """
    endung = os.path.splitext(pfad)[1].lower()
    if endung in DIREKT_CONTAINER:
        return {"modus": "direkt", "ziel": endung, "grund": ""}
    if not _ffmpeg_da():
        return {"modus": "nein", "ziel": "",
                "grund": "ffmpeg steht in diesem Container nicht bereit."}
    spuren = pruefe_datei(pfad)
    if not spuren:
        return {"modus": "nein", "ziel": "",
                "grund": "Die Datei liess sich nicht lesen."}
    video = spuren.get("video", "")
    if not video:                       # reine Tonspur in fremder Huelle
        ziel = ".m4a" if spuren.get("audio") in ("aac", "alac") else ".opus"
        return {"modus": "umpacken", "ziel": ziel, "grund": "",
                "spuren": spuren, "nur_ton": True}
    ziel = _VIDEO_ZIEL.get(video)
    if not ziel:
        return {"modus": "nein", "ziel": "", "spuren": spuren,
                "grund": f"Die Bildspur ({video or 'unbekannt'}) kann kein "
                         f"Browser darstellen. Neu kodieren waere kein "
                         f"Umpacken mehr -- lade die Datei besser herunter."}
    return {"modus": "umpacken", "ziel": ziel, "grund": "", "spuren": spuren}


def _schluessel(pfad: str, ziel: str) -> str:
    """Eindeutig je Datei UND Fassung: aendert sich die Quelle, faellt der
    Zwischenspeicher automatisch aus dem Rennen."""
    try:
        st = os.stat(pfad)
        stempel = f"{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        stempel = "0:0"
    roh = f"{os.path.realpath(pfad)}|{stempel}|{ziel}".encode()
    return hashlib.sha256(roh).hexdigest()[:32] + ziel


def cache_pfad(pfad: str, ziel: str) -> Path:
    return CACHE_DIR / _schluessel(pfad, ziel)


def _aufraeumen(max_bytes: int, schonen: Path = None):
    """Aelteste Zwischendateien werfen, bis die Obergrenze wieder passt.

    0 (oder weniger) heisst "unbegrenzt" -- dann passiert hier nichts. Frueher
    lief die Schleife mit max_bytes=0 durch und warf ALLES ausser der gerade
    fertigen Datei weg: die Einstellung bewirkte das Gegenteil ihres Textes.
    """
    if max_bytes <= 0:
        return
    try:
        dateien = [p for p in CACHE_DIR.glob("*") if p.is_file()
                   and not p.name.endswith(".teil")]
    except OSError:
        return
    gesamt = 0
    with_stat = []
    for p in dateien:
        try:
            st = p.stat()
        except OSError:
            continue
        gesamt += st.st_size
        with_stat.append((st.st_mtime, st.st_size, p))
    with_stat.sort()
    for _, groesse, p in with_stat:
        if gesamt <= max_bytes:
            break
        if schonen and p == schonen:
            continue
        try:
            p.unlink()
            gesamt -= groesse
        except OSError:
            pass


def status(pfad: str, ziel: str) -> dict:
    """Wo steht die Aufbereitung dieser Datei gerade?"""
    ablage = cache_pfad(pfad, ziel)
    if ablage.is_file() and ablage.stat().st_size > 0:
        return {"bereit": True, "fortschritt": 100, "fehler": ""}
    with _lock:
        lauf = _laeuft.get(ablage.name)
    if lauf:
        return {"bereit": False, "fortschritt": lauf.get("fortschritt", 0),
                "fehler": lauf.get("fehler", "")}
    return {"bereit": False, "fortschritt": 0, "fehler": ""}


def vorbereiten(pfad: str, max_cache_bytes: int) -> dict:
    """Das Umpacken anstossen (oder melden, dass es schon laeuft/fertig ist)."""
    plan = plan_fuer(pfad)
    if plan["modus"] != "umpacken":
        return plan
    ablage = cache_pfad(pfad, plan["ziel"])
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if ablage.is_file() and ablage.stat().st_size > 0:
        os.utime(ablage, None)                  # als zuletzt benutzt merken
        return {**plan, "bereit": True, "fortschritt": 100}
    with _lock:
        if ablage.name in _laeuft:
            return {**plan, "bereit": False,
                    "fortschritt": _laeuft[ablage.name].get("fortschritt", 0)}
        _laeuft[ablage.name] = {"fortschritt": 0, "fehler": ""}
    t = threading.Thread(target=_umpacken, name="remux",
                         args=(pfad, plan, ablage, max_cache_bytes), daemon=True)
    t.start()
    return {**plan, "bereit": False, "fortschritt": 0}


def _umpacken(pfad: str, plan: dict, ablage: Path, max_cache_bytes: int):
    """Der eigentliche ffmpeg-Lauf. Laeuft in einem eigenen Thread."""
    spuren = plan.get("spuren") or {}
    ziel = plan["ziel"]
    audio = (spuren.get("audio") or "").lower()
    teil = ablage.with_suffix(ablage.suffix + ".teil")

    befehl = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", pfad]
    if plan.get("nur_ton"):
        befehl += ["-vn", "-map", "0:a:0"]
    else:
        # Nur die erste Bild- und Tonspur: mehr braucht ein Browser-Player
        # nicht, und jede weitere Spur waere unnoetige Schreibarbeit.
        befehl += ["-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy"]
    if audio and audio in _AUDIO_OK.get(ziel, set()):
        befehl += ["-c:a", "copy"]
    else:
        codec, rate = _AUDIO_ERSATZ[ziel]
        befehl += ["-c:a", codec, "-b:a", rate]
    if ziel in (".mp4", ".m4a"):
        # Der Index gehoert an den Anfang, sonst kann der Player erst spulen,
        # wenn die ganze Datei geladen ist.
        befehl += ["-movflags", "+faststart"]
    befehl += ["-sn", "-dn", "-f", _FFMPEG_FORMAT.get(ziel, "mp4"),
               "-progress", "pipe:1", "-nostats", str(teil)]

    dauer = float(spuren.get("dauer") or 0)
    proc = None
    waechter = None
    abgeschossen = threading.Event()
    # Fehlermeldungen von ffmpeg: die ERSTEN Zeilen benennen die Ursache,
    # der Rest wiederholt sich. Nur die behalten, damit ein Lauf mit
    # tausenden Warnungen weder den Speicher fuellt noch die Ursache verdraengt.
    fehlerzeilen = []
    _MAX_FEHLERZEILEN = 12
    try:
        # stderr laeuft in DENSELBEN Strom wie der Fortschritt. Mit zwei
        # getrennten Pipes, von denen nur eine gelesen wird, blockiert ffmpeg,
        # sobald der stderr-Puffer (64 KB) voll ist -- und der Umpacker haengt
        # ewig, weil er auf das Ende von stdout wartet.
        proc = subprocess.Popen(befehl, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                errors="replace")

        def _abschiessen():
            abgeschossen.set()
            try:
                proc.kill()
            except OSError:
                pass

        waechter = threading.Timer(_UMPACK_ZEITLIMIT, _abschiessen)
        waechter.daemon = True
        waechter.start()
        for zeile in proc.stdout:
            if zeile.startswith("out_time_ms="):
                if dauer > 0:
                    try:
                        sek = int(zeile.split("=", 1)[1]) / 1_000_000
                    except ValueError:
                        continue
                    with _lock:
                        if ablage.name in _laeuft:
                            _laeuft[ablage.name]["fortschritt"] = \
                                max(1, min(99, int(sek / dauer * 100)))
            elif not _PROGRESS_ZEILE.match(zeile):
                zeile = zeile.strip()
                if zeile and len(fehlerzeilen) < _MAX_FEHLERZEILEN:
                    fehlerzeilen.append(zeile)
        proc.wait(timeout=60)
        fehlertext = "\n".join(fehlerzeilen)
        if len(fehlertext) > 400:          # der Anfang benennt die Ursache
            fehlertext = fehlertext[:400] + " …"
        if abgeschossen.is_set():
            raise RuntimeError(f"Umpacken nach {_UMPACK_ZEITLIMIT // 60} Minuten "
                               "abgebrochen -- die Datei liess sich nicht in "
                               "vertretbarer Zeit aufbereiten.")
        if proc.returncode == 0 and teil.is_file() and teil.stat().st_size > 0:
            os.replace(teil, ablage)
            _aufraeumen(max_cache_bytes, schonen=ablage)
            with _lock:
                _laeuft.pop(ablage.name, None)
            return
        raise RuntimeError(fehlertext or f"ffmpeg endete mit {proc.returncode}")
    except Exception as e:                       # noqa: BLE001 - alles melden
        with _lock:
            if ablage.name in _laeuft:
                _laeuft[ablage.name]["fehler"] = str(e)[:400]
        # Die Fehlermeldung soll der Nutzer sehen; damit sie nicht ewig haengen
        # bleibt, verfaellt sie nach ein paar Minuten von selbst.
        verfall = threading.Timer(300, lambda: _laeuft.pop(ablage.name, None))
        verfall.daemon = True             # darf das Beenden nicht aufhalten
        verfall.start()
    finally:
        if waechter is not None:
            waechter.cancel()
        if proc is not None and proc.poll() is None:
            # Ausnahme oder Zeitueberschreitung: kein ffmpeg-Zombie hinterlassen.
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=10)
            except subprocess.SubprocessError:
                pass
        try:
            if proc is not None and proc.stdout:
                proc.stdout.close()
        except OSError:
            pass
        try:
            if teil.exists():
                teil.unlink()
        except OSError:
            pass


def cache_groesse() -> int:
    try:
        return sum(p.stat().st_size for p in CACHE_DIR.glob("*") if p.is_file())
    except OSError:
        return 0


def cache_leeren() -> int:
    """Alles wegwerfen, was nicht gerade geschrieben wird."""
    weg = 0
    with _lock:
        aktiv = set(_laeuft)
    for p in list(CACHE_DIR.glob("*")) if CACHE_DIR.exists() else []:
        if not p.is_file() or p.name in aktiv or p.name.endswith(".teil"):
            continue
        try:
            weg += p.stat().st_size
            p.unlink()
        except OSError:
            pass
    return weg

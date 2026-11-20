"""End-to-End-Test der Download-Engine gegen einen lokalen HTTP-Server.

Deckt genau die gemeldeten Probleme ab: Abbruch landet als "abgebrochen" statt
als "Fehler", Teil-Download bleibt fuer die Wiederaufnahme liegen, Wiederaufnahme
laeuft durch, und der Fortschritt wird gedrosselt gesendet.
"""
import http.server
import json
import os
import socketserver
import sys
import tempfile
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent   # Deploy-Wurzel
ROOT = tempfile.mkdtemp(prefix="engine-")
CFG, DL = Path(ROOT) / "config", Path(ROOT) / "downloads"
CFG.mkdir(parents=True), DL.mkdir(parents=True)
# Ohne ffmpeg lokal: Nachbearbeitung aus, sonst scheitern die Postprocessoren.
(CFG / "settings.json").write_text(json.dumps({
    "default_dest": str(DL), "embed_metadata": False, "embed_thumbnail": False,
    "concurrent_downloads": 2, "output_template": "%(title)s.%(ext)s",
    "video_container": "auto",
}))
os.environ.update(CONFIG_DIR=str(CFG), DOWNLOADS_DIR=str(DL),
                  HOSTFS_DIR=str(Path(ROOT) / "hostfs"),
                  SHARES_DIR=str(Path(ROOT) / "shares"),
                  # Der Testserver lauscht auf 127.0.0.1 -- seit 2.5.1 werden
                  # solche Ziele ohne diesen Schalter abgelehnt.
                  DOWNLOAD_ALLOW_PRIVATE="1")
sys.path.insert(0, str(BASE / "app"))

import downloader                                            # noqa: E402
from config import get_settings                              # noqa: E402

SIZE = 30 * 1024 * 1024
ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  \033[32mOK\033[0m   {name}")
    else:
        fail += 1
        print(f"  \033[31mFAIL\033[0m {name} {extra}")


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, start, end):
        total = end - start + 1
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(total))
        self.send_header("Accept-Ranges", "bytes")
        if start:
            self.send_header("Content-Range", f"bytes {start}-{end}/{SIZE}")
        self.end_headers()
        return total

    def do_HEAD(self):
        self.send_response(200)
        self._send(0, SIZE - 1)

    def do_GET(self):
        rng = self.headers.get("Range")
        start = 0
        if rng and rng.startswith("bytes="):
            start = int(rng.split("=")[1].split("-")[0] or 0)
        self.send_response(206 if start else 200)
        RESUMED.append(start)
        total = self._send(start, SIZE - 1)
        chunk = b"\0" * 32768
        sent = 0
        try:
            while sent < total:
                n = min(len(chunk), total - sent)
                self.wfile.write(chunk[:n])
                sent += n
                time.sleep(0.006)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *a):
        pass


class Srv(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


RESUMED = []
events = []
downloader.subscribe(lambda e: events.append(e))

srv = Srv(("127.0.0.1", 0), Handler)
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.2)
URL = f"http://127.0.0.1:{PORT}/Testclip-A.mp4"
URL_B = f"http://127.0.0.1:{PORT}/Testclip-B.mp4"
URL_C = f"http://127.0.0.1:{PORT}/Testclip-C.mp4"

downloader.start()


def wait_status(jid, wanted, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        rec = downloader.history_one(jid)
        if rec and rec["status"] in wanted:
            return rec
        time.sleep(0.15)
    return downloader.history_one(jid)


print("\n── 1) Normaler Download laeuft durch ──")
j = downloader.enqueue(URL)
rec = wait_status(j["id"], ("done", "error", "canceled"))
check("Status = done", rec and rec["status"] == "done",
      f'-> {rec and rec["status"]}: {rec and rec["error"][:120]}')
check("Zieldatei erfasst", bool(rec["files"]) and Path(rec["files"][0]).is_file(),
      f'-> {rec["files"]}')
check("Dateigroesse aus dem Dateisystem", rec["filesize"] == SIZE,
      f'-> {rec["filesize"]} statt {SIZE}')
check("Dateiname gesetzt", rec["filename"].endswith(".mp4"), f'-> {rec["filename"]}')
check("Endung im Verlauf", rec["ext"] == "mp4", f'-> {rec["ext"]}')
check("playable-Flag", rec["playable"] is True)
check("Kein Fehlertext", rec["error"] == "", f'-> {rec["error"][:120]}')

check("Datei liegt im Unterordner Quelle/Art/Jahr/Monat",
      rec["files"] and Path(rec["files"][0]).parent
      == DL / "127.0.0.1" / "video" / time.strftime("%Y") / time.strftime("%m"),
      f'-> {rec["files"] and Path(rec["files"][0]).parent}')
check("Nichts liegt mehr flach im Zielordner",
      not [f for f in DL.iterdir() if f.is_file()],
      f"-> {[f.name for f in DL.iterdir() if f.is_file()]}")

print("\n── 2) Fortschritt wird gedrosselt gesendet ──")
prog = [e for e in events if e.get("type") == "job"]
dauer = 0.5
check(f"Nur {len(prog)} Ereignisse fuer 30 MB (ungedrosselt waeren es hunderte)",
      len(prog) < 80, f"-> {len(prog)}")
check("Fortschritt kam ueberhaupt an", len(prog) > 3, f"-> {len(prog)}")

print("\n── 3) Abbruch mitten im Download ──")
RESUMED.clear()
j2 = downloader.enqueue(URL_B)
# warten, bis wirklich Daten fliessen
end = time.time() + 30
while time.time() < end:
    with downloader._jobs_lock:
        job = downloader._jobs.get(j2["id"])
    if job and job["status"] == "downloading" and job["downloaded"] > 1_000_000:
        break
    time.sleep(0.1)
with downloader._jobs_lock:
    vorher = downloader._jobs[j2["id"]]["downloaded"]
check("Download war in Fahrt", vorher > 1_000_000, f"-> {vorher} Bytes")

check("cancel() bestaetigt", downloader.cancel(j2["id"]) is True)
with downloader._jobs_lock:
    zwischen = downloader._jobs[j2["id"]]["status"]
check("Zwischenstatus 'canceling' sichtbar", zwischen == "canceling", f"-> {zwischen}")

rec2 = wait_status(j2["id"], ("canceled", "done", "error"), timeout=40)
check("Status = canceled (NICHT error) -- das war der gemeldete Fehler",
      rec2 and rec2["status"] == "canceled",
      f'-> {rec2 and rec2["status"]}: {rec2 and rec2["error"][:160]}')
parts = list(DL.rglob("*.part"))
check("Teil-Download bleibt fuer die Wiederaufnahme liegen", len(parts) == 1,
      f"-> {[p.name for p in parts]}")
teil = parts[0].stat().st_size if parts else 0
check("Teil-Datei enthaelt bereits Daten", 0 < teil < SIZE, f"-> {teil} Bytes")

print("\n── 4) Wiederaufnahme setzt fort statt neu zu laden ──")
RESUMED.clear()
r = downloader.retry(rec2["id"])
check("retry() reiht neu ein", r is not None and r["url"] == rec2["url"])
rec3 = wait_status(r["id"], ("done", "error", "canceled"), timeout=90)
check("Wiederaufnahme wird fertig", rec3 and rec3["status"] == "done",
      f'-> {rec3 and rec3["status"]}: {rec3 and rec3["error"][:160]}')
check("Server bekam eine Range-Anfrage -> es wurde FORTGESETZT",
      any(s > 0 for s in RESUMED), f"-> Startoffsets {RESUMED}")
check("Datei ist vollstaendig",
      rec3 and rec3["files"] and Path(rec3["files"][0]).stat().st_size == SIZE)
check("Keine .part-Reste mehr", not list(DL.rglob("*.part")))

print("\n── 5) Abbruch in der Warteschlange ──")
with downloader._jobs_lock:
    downloader._worker_target = 0          # Worker anhalten
time.sleep(1.6)
j4 = downloader.enqueue(URL_C)
downloader.cancel(j4["id"])
rec4 = downloader.history_one(j4["id"])
check("Wartender Job wird sofort abgebrochen",
      rec4 and rec4["status"] == "canceled", f'-> {rec4 and rec4["status"]}')

print("\n── 6) Rubriken und Zaehler ──")
counts = downloader.history_counts()
check("Rubrik 'fertig' zaehlt", counts.get("done", 0) >= 2, f"-> {counts}")
check("Rubrik 'abgebrochen' zaehlt", counts.get("canceled", 0) >= 2, f"-> {counts}")
check("Filter liefert nur die Rubrik",
      all(h["status"] == "canceled" for h in downloader.history("canceled")))
check("Suche greift", len(downloader.history("", 300, "Testclip")) > 0)

print("\n── 7) Zielformat erzeugt den richtigen Postprocessor ──")
s = get_settings()
for cont in ("mkv", "mp4", "webm", "mov"):
    job = downloader._new_job(URL, {"video_container": cont, "dest": str(DL)})
    o = downloader._build_opts(job, s)
    pps = [p for p in o["postprocessors"] if p["key"] == "FFmpegVideoRemuxer"]
    check(f"Container {cont}: merge + Remuxer gesetzt",
          o.get("merge_output_format") == cont and len(pps) == 1
          and pps[0]["preferedformat"] == cont,
          f'-> merge={o.get("merge_output_format")} pps={pps}')

job = downloader._new_job(URL, {"video_container": "auto", "dest": str(DL)})
o = downloader._build_opts(job, s)
check("Container auto: kein Umpacken erzwungen",
      "merge_output_format" not in o
      and not [p for p in o["postprocessors"] if p["key"] == "FFmpegVideoRemuxer"])

job = downloader._new_job(URL, {"format_mode": "audio", "audio_format": "opus",
                                "video_container": "mkv", "dest": str(DL)})
o = downloader._build_opts(job, s)
check("Nur-Audio ignoriert den Video-Container",
      "merge_output_format" not in o
      and any(p["key"] == "FFmpegExtractAudio" for p in o["postprocessors"]))

job = downloader._new_job(URL, {"format_mode": "mp4", "dest": str(DL)})
o = downloader._build_opts(job, s)
check("Alter Modus 'mp4' wird auf Container mp4 abgebildet",
      o.get("merge_output_format") == "mp4")

print("\n── 7b) Playlist oder Einzelvideo ──")
job = downloader._new_job(URL, {"dest": str(DL)})
o = downloader._build_opts(job, s)
check("Standard laedt die ganze Liste", o.get("noplaylist") is False,
      f'-> {o.get("noplaylist")}')
job = downloader._new_job(URL, {"dest": str(DL), "playlist_mode": "single"})
o_s = downloader._build_opts(job, s)
check("'single' laedt nur den verlinkten Titel", o_s.get("noplaylist") is True,
      f'-> {o_s.get("noplaylist")}')
job = downloader._new_job(URL, {"dest": str(DL), "playlist_mode": "playlist"})
check("'playlist' erzwingt die Liste",
      downloader._build_opts(job, s).get("noplaylist") is False)
job = downloader._new_job(URL, {"dest": str(DL), "playlist_mode": "quatsch"})
check("Unbekannter Wert faellt auf die Liste zurueck",
      downloader._build_opts(job, s).get("noplaylist") is False)
from config import save_settings                             # noqa: E402
s_single = save_settings({"playlist_mode": "single"})
check("Die Einstellung wirkt als Vorgabe",
      downloader._build_opts(downloader._new_job(URL, {"dest": str(DL)}),
                             s_single).get("noplaylist") is True)
check("Der Auftrag schlaegt die Vorgabe",
      downloader._build_opts(
          downloader._new_job(URL, {"dest": str(DL), "playlist_mode": "playlist"}),
          s_single).get("noplaylist") is False)
s = save_settings({"playlist_mode": "playlist"})       # Ausgangslage zurueck

check("Teil-Downloads bleiben aktiviert (Wiederaufnahme)",
      o["continuedl"] is True and o["nopart"] is False)
check("ignoreerrors nur fuer Downloads", o["ignoreerrors"] == "only_download")
UNTER = Path("127.0.0.1") / "video" / time.strftime("%Y") / time.strftime("%m")
check("Zielordner ueber paths.home", o["paths"]["home"].startswith(str(DL)))
check("Unterordner nach Quelle/Art/Jahr/Monat",
      o["paths"]["home"] == str(DL / UNTER), f'-> {o["paths"]["home"]}')

print("\n── 8) Protokoll pro Job ──")
bad = downloader.enqueue("http://127.0.0.1:1/gibtsnicht.mp4")
recb = wait_status(bad["id"], ("error", "done", "canceled"), timeout=60)
check("Fehlschlag landet als 'error'", recb and recb["status"] == "error",
      f'-> {recb and recb["status"]}')
check("Echte yt-dlp-Meldung statt Platzhalter",
      recb and recb["error"] and "Seite nicht unterstuetzt" not in recb["error"],
      f'-> {recb and recb["error"][:160]}')
check("Protokoll wurde aufgezeichnet", bool(recb and recb["log"]),
      f'-> {(recb and recb["log"] or "")[:120]}')

print("\n── 9) Zeitlimit je Auftrag ──")
check("socket_timeout ist gesetzt (eingeschlafene Verbindung haengt nicht ewig)",
      o.get("socket_timeout") == 30, f'-> {o.get("socket_timeout")}')
check("DOWNLOAD_ALLOW_PRIVATE laesst die Testquelle zu",
      downloader.url_pruefen(URL) == "", f"-> {downloader.url_pruefen(URL)!r}")
check("Standard-Zeitlimit: 6 h aus den Einstellungen",
      downloader._zeitlimit_sekunden(get_settings()) == 6 * 3600)
check("0 = kein Limit, kein Waechter",
      downloader._zeitlimit_sekunden({"job_timeout_h": 0}) == 0
      and downloader._waechter_starten({"status": "downloading"}, 0) is None)
_orig_limit = downloader._zeitlimit_sekunden
downloader._zeitlimit_sekunden = lambda s: 2          # der 30-MB-Clip braucht ~6 s
downloader._ensure_workers()                            # Worker wieder anwerfen
RESUMED.clear()
URL_T = f"http://127.0.0.1:{PORT}/Testclip-T.mp4"
t0 = time.time()
jt = downloader.enqueue(URL_T)
rect = wait_status(jt["id"], ("done", "error", "canceled"), timeout=40)
dauer_t = time.time() - t0
check("Auftrag wird nach Ablauf abgebrochen und als FEHLER verbucht",
      rect and rect["status"] == "error",
      f'-> {rect and rect["status"]}: {rect and rect["error"][:120]}')
check("Fehlertext nennt das Zeitlimit",
      rect and "Zeitlimit" in rect["error"], f'-> {rect and rect["error"][:120]}')
check("Abbruch kam zeitnah (nicht erst am Ende des Downloads)", dauer_t < 8,
      f"-> {dauer_t:.1f} s")
check("Teil-Download bleibt fuer Fortsetzen liegen",
      any(p.name.startswith("Testclip-T") for p in DL.rglob("*.part")),
      f"-> {[p.name for p in DL.rglob('*.part')]}")
downloader._zeitlimit_sekunden = _orig_limit
with downloader._jobs_lock:
    jobt = downloader._jobs.get(jt["id"])
check("Job-Zustand ist abgeschlossen (Worker ist frei)",
      jobt is not None and jobt["status"] not in downloader.ACTIVE)
rt = downloader.retry(jt["id"])
rect2 = wait_status(rt["id"], ("done", "error", "canceled"), timeout=90)
check("Fortsetzen nach Zeitlimit laeuft durch (Worker war frei)",
      rect2 and rect2["status"] == "done",
      f'-> {rect2 and rect2["status"]}: {rect2 and rect2["error"][:120]}')
check("... und setzte per Range fort", any(s_ > 0 for s_ in RESUMED),
      f"-> {RESUMED}")

print("\n── 10) Duplikate erkennt enqueue() selbst ──")
URL_D = f"http://127.0.0.1:{PORT}/Testclip-D.mp4"
jd = downloader.enqueue(URL_D)                    # laeuft ~6 s -- genug Zeit
try:
    downloader.enqueue(URL_D)
    check("Zweites Einreihen derselben URL wirft Duplikat", False, "-> kein Fehler")
except downloader.Duplikat:
    check("Zweites Einreihen derselben URL wirft Duplikat", True)
check("Nur EIN Auftrag fuer die URL in der Warteschlange",
      sum(1 for j_ in downloader.list_jobs() if j_["url"] == URL_D) == 1)
downloader.cancel(jd["id"])
# "canceling" zaehlt noch als aktiv -- erst nach dem echten Ende ist die URL frei.
recd = wait_status(jd["id"], ("canceled", "done", "error"), timeout=40)
check("Solange der Auftrag aktiv ist, bleibt die URL gesperrt; danach ist sie frei",
      recd is not None and recd["status"] not in downloader.ACTIVE)
try:
    jd2 = downloader.enqueue(URL_D)
    check("Nach dem Ende darf die URL erneut eingereiht werden", True)
    downloader.cancel(jd2["id"])
    wait_status(jd2["id"], ("canceled", "done", "error"), timeout=40)
except downloader.Duplikat:
    check("Nach dem Ende darf die URL erneut eingereiht werden", False)
try:
    downloader.enqueue("ftp://127.0.0.1/x.mp4")
    check("enqueue lehnt fremde Schemata ab", False, "-> kein Fehler")
except downloader.Duplikat:
    check("enqueue lehnt fremde Schemata ab", False, "-> als Duplikat gemeldet")
except ValueError as e:
    check("enqueue lehnt fremde Schemata ab", "http" in str(e), f"-> {e}")


print("\n── 11) Browser-Tarnung: HTTP 403 wird mit Impersonation wiederholt ──")
# Manche Portale (PornHub, Cloudflare-Seiten) weisen die urllib-Signatur mit
# 403 ab. yt-dlp kann sich mit curl_cffi als Browser tarnen -- der Downloader
# muss das (a) auf Wunsch immer setzen und (b) im Automatikmodus nach einem
# 403 selbst nachlegen. Der echte yt-dlp-Aufruf wird hier durch eine Attrappe
# ersetzt: Tarnung laesst sich ohne Netz nicht nachstellen.
import types                                                  # noqa: E402
import yt_dlp                                                 # noqa: E402

_orig_ytdlp = downloader.yt_dlp
_orig_verfuegbar = downloader._tarnung_verfuegbar
AUFRUFE = []


class _FakeYDL:
    def __init__(self, opts):
        self.opts = opts
        AUFRUFE.append(dict(opts))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True):
        name = url.rsplit("/", 1)[-1]
        if name.startswith("Tarn-404"):
            self.opts["logger"].error("ERROR: [generic] x: Unable to download webpage: HTTP Error 404: Not Found")
            raise yt_dlp.utils.DownloadError("ERROR: HTTP Error 404: Not Found")
        if "impersonate" not in self.opts:
            self.opts["logger"].error(
                "ERROR: [PornHub] 65c8c450d2897: Unable to download webpage: "
                "HTTP Error 403: Forbidden (caused by <HTTPError 403: Forbidden>)")
            raise yt_dlp.utils.DownloadError("ERROR: HTTP Error 403: Forbidden")
        ziel = Path(self.opts["paths"]["home"])
        ziel.mkdir(parents=True, exist_ok=True)
        f = ziel / name
        f.write_bytes(b"\0" * 1024)
        return {"title": "Getarnt", "requested_downloads": [{"filepath": str(f)}]}


downloader.yt_dlp = types.SimpleNamespace(YoutubeDL=_FakeYDL)
downloader._tarnung_verfuegbar = lambda: True
try:
    s = save_settings({"browser_tarnung": "auto"})
    check("Standard: Tarnung 'auto'", s["browser_tarnung"] == "auto", f'-> {s["browser_tarnung"]}')
    o_auto = downloader._build_opts(downloader._new_job(URL, {"dest": str(DL)}), s)
    check("'auto' setzt beim ersten Versuch KEINE globale Tarnung", "impersonate" not in o_auto)
    o_immer = downloader._build_opts(downloader._new_job(URL, {"dest": str(DL)}),
                                     save_settings({"browser_tarnung": "immer"}))
    check("'immer' tarnt jede Anfrage als Chrome",
          str(o_immer.get("impersonate", "")).startswith("chrome"), f'-> {o_immer.get("impersonate")!r}')
    o_aus = downloader._build_opts(downloader._new_job(URL, {"dest": str(DL)}),
                                   save_settings({"browser_tarnung": "aus"}))
    check("'aus' setzt keine Tarnung", "impersonate" not in o_aus)
    check("Unbekannter Wert faellt auf 'auto' zurueck",
          save_settings({"browser_tarnung": "quatsch"})["browser_tarnung"] == "auto")

    save_settings({"browser_tarnung": "auto"})
    AUFRUFE.clear()
    jt = downloader.enqueue(f"http://127.0.0.1:{PORT}/Tarn-A.mp4")
    rect = wait_status(jt["id"], ("done", "error", "canceled"), timeout=30)
    check("403 -> zweiter Versuch mit Tarnung laeuft durch",
          rect and rect["status"] == "done", f'-> {rect and rect["status"]}: {rect and rect["error"][:120]}')
    check("Genau zwei yt-dlp-Laeufe", len(AUFRUFE) == 2, f"-> {len(AUFRUFE)}")
    check("Der zweite Lauf ist getarnt",
          len(AUFRUFE) == 2 and "impersonate" not in AUFRUFE[0] and "impersonate" in AUFRUFE[1])
    check("Protokoll erklaert die Wiederholung",
          rect and any("Tarnung" in z for z in rect["log"].splitlines()), f'-> {rect and rect["log"][-200:]}')
    check("Kein Fehlertext aus dem ersten Versuch bleibt haengen",
          rect and rect["error"] == "" and rect["title"] == "Getarnt", f'-> {rect and rect["error"]!r}')

    AUFRUFE.clear()
    jt = downloader.enqueue(f"http://127.0.0.1:{PORT}/Tarn-404.mp4")
    rect = wait_status(jt["id"], ("done", "error", "canceled"), timeout=30)
    check("Andere Fehler (404) werden NICHT wiederholt",
          rect and rect["status"] == "error" and len(AUFRUFE) == 1, f'-> {rect and rect["status"]}, {len(AUFRUFE)} Laeufe')

    save_settings({"browser_tarnung": "aus"})
    AUFRUFE.clear()
    jt = downloader.enqueue(f"http://127.0.0.1:{PORT}/Tarn-B.mp4")
    rect = wait_status(jt["id"], ("done", "error", "canceled"), timeout=30)
    check("'aus': 403 bleibt ein Fehler, kein zweiter Versuch",
          rect and rect["status"] == "error" and len(AUFRUFE) == 1 and "403" in rect["error"],
          f'-> {rect and rect["status"]}, {len(AUFRUFE)} Laeufe, {rect and rect["error"][:80]}')

    save_settings({"browser_tarnung": "auto"})
    downloader._tarnung_verfuegbar = lambda: False
    AUFRUFE.clear()
    jt = downloader.enqueue(f"http://127.0.0.1:{PORT}/Tarn-C.mp4")
    rect = wait_status(jt["id"], ("done", "error", "canceled"), timeout=30)
    check("Ohne curl_cffi: kein zweiter Versuch, Protokoll nennt die fehlende Tarnung",
          rect and rect["status"] == "error" and len(AUFRUFE) == 1
          and "curl_cffi" in rect["log"], f'-> {rect and rect["status"]}, {len(AUFRUFE)} Laeufe')
finally:
    downloader.yt_dlp = _orig_ytdlp
    downloader._tarnung_verfuegbar = _orig_verfuegbar
    save_settings({"browser_tarnung": "auto"})

print("\n── 12) Titelbild nur, wo das Zielformat eins tragen kann ──")
# yt-dlp bettet Titelbilder nur in mp3, mkv/mka, ogg/opus/flac und
# m4a/mp4/m4v/mov ein. Bei jedem anderen Ziel (WAV!) bricht die
# Nachbearbeitung ab -- VOR dem Verschieben aus dem Zwischenordner. Am
# 30.09.2026 lag so eine WAV unerreichbar in config/tmp, der Verlauf meldete
# "fertig", Ansehen und Laden antworteten "Datei existiert nicht mehr".
s_bild = save_settings({"embed_thumbnail": True})


def _mit_titelbild(o):
    return (any(p["key"] == "EmbedThumbnail" for p in o["postprocessors"])
            and bool(o.get("writethumbnail")))


def _ohne_titelbild(o):
    return (not any(p["key"] == "EmbedThumbnail" for p in o["postprocessors"])
            and not o.get("writethumbnail"))


for fmt in ("mp3", "m4a", "opus", "flac"):
    o = downloader._build_opts(downloader._new_job(
        URL, {"format_mode": "audio", "audio_format": fmt, "dest": str(DL)}), s_bild)
    check(f"Audio {fmt}: Titelbild wird eingebettet", _mit_titelbild(o))
job = downloader._new_job(URL, {"format_mode": "audio", "audio_format": "wav",
                                "dest": str(DL)})
o = downloader._build_opts(job, s_bild)
check("Audio wav: KEIN Titelbild (WAV kann keins tragen)", _ohne_titelbild(o),
      f'-> {[p["key"] for p in o["postprocessors"]]}, writethumbnail={o.get("writethumbnail")}')
check("Protokoll erklaert das fehlende Titelbild",
      any("Titelbild" in z for z in job["log"]), f'-> {list(job["log"])[-2:]}')
for cont in ("auto", "mkv", "mp4", "mov"):
    o = downloader._build_opts(downloader._new_job(
        URL, {"video_container": cont, "dest": str(DL)}), s_bild)
    check(f"Video {cont}: Titelbild wird eingebettet", _mit_titelbild(o))
o = downloader._build_opts(downloader._new_job(
    URL, {"video_container": "webm", "dest": str(DL)}), s_bild)
check("Video webm: KEIN Titelbild (webm kann keins tragen)", _ohne_titelbild(o))
save_settings({"embed_thumbnail": False})

print("\n── 12b) Nachbearbeitung bricht ab: Datei kommt trotzdem ans Ziel ──")
# Sicherheitsnetz fuer jeden anderen Nachbearbeitungs-Fehler: yt-dlp kehrt
# dann normal zurueck (ignoreerrors='only_download'), hat aber nicht mehr aus
# dem Zwischenordner verschoben.
PP_FEHLER = ("ERROR: Postprocessing: Supported filetypes for thumbnail embedding "
             "are: mp3, mkv/mka, ogg/opus/flac, m4a/mp4/m4v/mov")
STAMM = "Father Forgive Me | The Homecoming [ZWeKDIxxeAc]"


class _FakeAbbruch:
    inhalt = b"RIFF-eins"

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True):
        tmp = Path(self.opts["paths"]["temp"])
        tmp.mkdir(parents=True, exist_ok=True)
        wav = tmp / f"{STAMM}.wav"
        wav.write_bytes(_FakeAbbruch.inhalt)
        for endung in (".webp", ".png"):          # Titelbild-Reste wie auf dem NAS
            (tmp / f"{STAMM}{endung}").write_bytes(b"bild")
        self.opts["logger"].error(PP_FEHLER)
        return {"title": "Abbruch", "requested_downloads": [{"filepath": str(wav)}]}


TMP = Path(downloader.TEMP_DIR)
downloader.yt_dlp = types.SimpleNamespace(YoutubeDL=_FakeAbbruch)
try:
    save_settings({"local_temp": True})
    jt = downloader.enqueue(f"http://127.0.0.1:{PORT}/Abbruch.wav")
    reca = wait_status(jt["id"], ("done", "error", "canceled"), timeout=30)
    datei = (reca and reca["files"] or [""])[0]
    check("Auftrag gilt als fertig", reca and reca["status"] == "done",
          f'-> {reca and reca["status"]}: {reca and reca["error"]}')
    check("Datei liegt im Zielordner, nicht im Zwischenordner",
          reca and datei.startswith(reca["dest"] + os.sep) and os.path.isfile(datei)
          and not os.path.realpath(datei).startswith(os.path.realpath(TMP) + os.sep),
          f"-> {datei}")
    check("Inhalt unversehrt",
          os.path.isfile(datei) and Path(datei).read_bytes() == b"RIFF-eins")
    check("Zwischenordner geraeumt, auch die Titelbild-Reste",
          not [p for p in TMP.iterdir() if "ZWeKDIxxeAc" in p.name],
          f"-> {[p.name for p in TMP.iterdir()]}")
    check("Fehlertext nennt die abgebrochene Nachbearbeitung",
          reca and "Nachbearbeitung" in reca["error"] and "thumbnail" in reca["error"],
          f'-> {reca and reca["error"]!r}')
    check("Bibliothek sieht die Datei", reca and reca["exists"])

    # Gleicher Name noch einmal: die vorhandene Datei wird nie ueberschrieben.
    _FakeAbbruch.inhalt = b"RIFF-zwei"
    jt = downloader.enqueue(f"http://127.0.0.1:{PORT}/Abbruch.wav")
    recb = wait_status(jt["id"], ("done", "error", "canceled"), timeout=30)
    datei_b = (recb and recb["files"] or [""])[0]
    check("Gleichnamige Datei am Ziel: neuer Name statt Ueberschreiben",
          recb and recb["status"] == "done" and datei_b.endswith(f"{STAMM} (2).wav")
          and os.path.isfile(datei_b) and Path(datei_b).read_bytes() == b"RIFF-zwei",
          f"-> {datei_b}")
    check("Erste Datei unangetastet",
          os.path.isfile(datei) and Path(datei).read_bytes() == b"RIFF-eins")
finally:
    downloader.yt_dlp = _orig_ytdlp
    save_settings({"local_temp": False})

print("\n── 12c) Altlasten beim Start: fertige Eintraege mit Datei in config/tmp ──")
TMP.mkdir(parents=True, exist_ok=True)
ziel_alt = DL / "Youtube" / "audio" / "2026" / "09"
alt = TMP / "Eldruna - Father Forgive Me | The Homecoming [alt1].wav"
alt.write_bytes(b"RIFF-alt")
for endung in (".webp", ".png"):
    alt.with_suffix(endung).write_bytes(b"bild")
job_alt = downloader._new_job("https://www.youtube.com/watch?v=alt1",
                              {"dest": str(DL), "subdir": "Youtube/audio/2026/09"})
job_alt.update(status="done", dest=str(ziel_alt), files=[str(alt)], filename=alt.name)
downloader._record(job_alt)
# Abgebrochen: seine Reste braucht "Fortsetzen" -- die bleiben, wo sie sind.
rest = TMP / "Abgebrochen [rest1].webm"
rest.write_bytes(b"teil")
job_rest = downloader._new_job("https://www.youtube.com/watch?v=rest1", {"dest": str(DL)})
job_rest.update(status="canceled", dest=str(DL), files=[str(rest)])
downloader._record(job_rest)

downloader._zwischenordner_reste_retten()
rec = downloader.history_one(job_alt["id"])
neu = (rec and rec["files"] or [""])[0]
check("Altlast: Datei ans eingetragene Ziel verschoben",
      neu == str(ziel_alt / alt.name) and os.path.isfile(neu)
      and Path(neu).read_bytes() == b"RIFF-alt", f"-> {neu}")
check("Altlast: Zwischenordner geraeumt (Datei und Titelbild-Reste)",
      not alt.exists() and not alt.with_suffix(".webp").exists()
      and not alt.with_suffix(".png").exists())
check("Altlast: Bibliothek sieht die Datei", rec and rec["exists"])
check("Abgebrochener Eintrag bleibt unberuehrt (Fortsetzen braucht die Reste)",
      rest.exists() and downloader.history_one(job_rest["id"])["files"] == [str(rest)])
downloader._zwischenordner_reste_retten()
check("Zweiter Lauf aendert nichts",
      downloader.history_one(job_alt["id"])["files"] == [neu] and os.path.isfile(neu))

srv.shutdown()
print(f"\n{'='*54}\n  {ok} bestanden, {fail} fehlgeschlagen\n{'='*54}")
sys.exit(1 if fail else 0)

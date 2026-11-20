# ============================================================================
# Erdacht und programmiert von Fabian Hammer.
# (c) 2026 Fabian Hammer - Alle Rechte vorbehalten. Geistiges Eigentum.
# ============================================================================
"""Haertungen aus der Pruefung vom 17.09.2026 (v2.5.1).

Laeuft ohne Netz, ohne Docker, ohne echtes ffmpeg und ohne echtes mount:
beide Programme werden durch kleine Python-Attrappen im PATH ersetzt, die
genau das nachstellen, was frueher schiefging (Passwort in der Optionszeile,
ffmpeg blockiert an einem vollen stderr-Puffer).
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ROOT = tempfile.mkdtemp(prefix="sich-")
CFG, DL, HOST, SH = (Path(ROOT) / n for n in ("config", "downloads", "hostfs", "shares"))
for d in (CFG, DL, HOST, SH):
    d.mkdir(parents=True)
(HOST / "medien").mkdir()
(HOST / "video" / "serien").mkdir(parents=True)
(HOST / "andere").mkdir()

os.environ.update(
    CONFIG_DIR=str(CFG), DOWNLOADS_DIR=str(DL), HOSTFS_DIR=str(HOST),
    SHARES_DIR=str(SH), HOSTFS_HOST_PATH="/volume1",
    # Absichtlich unordentlich: Leerzeichen, fuehrender Schraegstrich, ein
    # Ausbruchsversuch, ein bereits mit dem Mount vorangestellter Pfad, ein
    # Duplikat und ein Ordner, den es nicht gibt.
    DOWNLOAD_ROOTS=f"medien , /video/serien, ../../etc, {HOST}/medien, fehlt",
)
os.environ.pop("DOWNLOAD_ALLOW_PRIVATE", None)
sys.path.insert(0, str(BASE / "app"))

import config                                                # noqa: E402
import auth                                                  # noqa: E402
import downloader                                            # noqa: E402
import playback                                              # noqa: E402
import server                                                # noqa: E402
import shares                                                # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  \033[32mOK\033[0m   {name}")
    else:
        fail += 1
        print(f"  \033[31mFAIL\033[0m {name} {extra}")


# ── Attrappen fuer mount und ffmpeg ──────────────────────────────────────────
FAKE_BIN = Path(ROOT) / "bin"
FAKE_BIN.mkdir()
MOUNT_LOG = Path(ROOT) / "mount.log"
MOUNT_FAIL = Path(ROOT) / "mount.fail"          # existiert -> mount scheitert

(FAKE_BIN / "mount").write_text(f'''#!{sys.executable}
import os, re, sys
argv = sys.argv[1:]
log = open({str(MOUNT_LOG)!r}, "a", encoding="utf-8")
log.write("ARGS " + " ".join(argv) + "\\n")
cred = None
for a in argv:
    m = re.search(r"credentials=([^,]+)", a)
    if m:
        cred = m.group(1)
if cred:
    st = os.stat(cred)
    log.write("CRED " + cred + "\\n")
    log.write("MODE %o\\n" % (st.st_mode & 0o777))
    log.write("INHALT " + open(cred, encoding="utf-8").read().replace("\\n", "|") + "\\n")
log.close()
if os.path.exists({str(MOUNT_FAIL)!r}):
    sys.stderr.write("mount error(13): Permission denied\\n")
    sys.stderr.write("options: " + " ".join(argv) + "\\n")
    if cred:
        sys.stderr.write(open(cred, encoding="utf-8").read())
    sys.stderr.write("Refer to the mount.cifs(8) manual page (e.g. man mount.cifs) "
                     "and kernel log messages (dmesg)\\n" * 5)
    sys.exit(32)
sys.exit(0)
''', encoding="utf-8")
(FAKE_BIN / "mount").chmod(0o755)

(FAKE_BIN / "ffmpeg").write_text(f'''#!{sys.executable}
import os, sys, time
modus = os.environ.get("FAKE_FFMPEG_MODE", "ok")
ziel = sys.argv[-1]
# Viel stderr -- deutlich mehr als der 64-KB-Pipe-Puffer. Mit zwei getrennten
# Pipes, von denen nur stdout gelesen wird, bleibt ffmpeg genau hier stehen.
sys.stderr.write("[matroska @ 0x1] kaputt: Erste Zeile benennt die Ursache\\n")
for i in range(6000):
    sys.stderr.write("[matroska @ 0x1] Warnung Nr. %d, unwichtig aber lang\\n" % i)
    if i % 500 == 0:
        sys.stderr.flush()
sys.stderr.flush()
if modus == "hang":
    time.sleep(120)
for i in range(1, 6):
    sys.stdout.write("frame=%d\\nout_time_ms=%d\\nprogress=continue\\n" % (i, i * 2_000_000))
    sys.stdout.flush()
if modus == "fail":
    sys.exit(1)
with open(ziel, "wb") as f:
    f.write(b"\\0" * 4096)
sys.stdout.write("progress=end\\n")
sys.exit(0)
''', encoding="utf-8")
(FAKE_BIN / "ffmpeg").chmod(0o755)
os.environ["PATH"] = str(FAKE_BIN) + os.pathsep + os.environ.get("PATH", "")


print("\n── 1) DOWNLOAD_ROOTS: nur benannte Teilbaeume des Host-Mounts ──")
check("Eintraege werden bereinigt, '..' fliegt raus, Duplikat verschmilzt",
      config.DOWNLOAD_ROOTS == [HOST / "medien", HOST / "video" / "serien", HOST / "fehlt"],
      f"-> {config.DOWNLOAD_ROOTS}")
roots = server._build_roots()
labels = [l for l, _ in roots]
check("Wurzeln: Downloads + genau die vorhandenen Teilbaeume",
      [p for _, p in roots] == [str(DL), str(HOST / "medien"), str(HOST / "video" / "serien")],
      f"-> {roots}")
check("Beschriftung nennt den Host-Pfad (HOSTFS_HOST_PATH)",
      "Host: /volume1/medien" in labels and "Host: /volume1/video/serien" in labels,
      f"-> {labels}")
check("Der ganze Mount ist NICHT mehr waehlbar", str(HOST) not in [p for _, p in roots])
check("Ziel im erlaubten Teilbaum geht durch",
      server._allowed_path(str(HOST / "medien" / "neu")) is not None)
check("Ziel im Mount, aber ausserhalb der Teilbaeume -> abgewiesen",
      server._allowed_path(str(HOST / "andere")) is None)
check("Der Mount selbst -> abgewiesen", server._allowed_path(str(HOST)) is None)
check("Ausbruch per .. aus dem Teilbaum -> abgewiesen",
      server._allowed_path(str(HOST / "medien" / ".." / "andere")) is None)
hinweis = server._roots_hinweis()
check("Hinweis fuer die Oberflaeche nennt die erlaubten Bereiche",
      "/volume1/medien" in hinweis and "/volume1/video/serien" in hinweis
      and "DOWNLOAD_ROOTS" in hinweis, f"-> {hinweis!r}")
check("... und den fehlenden Ordner", "Nicht gefunden" in hinweis and "fehlt" in hinweis,
      f"-> {hinweis!r}")
check("/api/roots-Endpunkt liefert den Hinweis mit",
      "hinweis" in __import__("asyncio").run(server.roots()))


print("\n── 2) Adress-Pruefung: nur http(s), nichts im eigenen Netz ──")
ABGELEHNT = [
    "http://127.0.0.1:8080/x", "http://127.5.5.5/x", "https://192.168.1.23/x",
    "http://172.16.0.1/x", "http://172.31.255.255/x", "http://192.168.1.1/x",
    "http://169.254.169.254/latest/meta-data", "http://[::1]/x", "http://[fd00::1]/x",
    "http://[fc00::1]/x", "http://[fe80::1]/x", "http://[::ffff:10.0.0.1]/x",
    "http://[::ffff:127.0.0.1]/x", "http://0.0.0.0/x", "http://localhost/x",
    "http://LOCALHOST:9000/x", "http://foo.localhost/x", "http://2130706433/x",
    "http://0x7f.0.0.1/x", "http://0177.0.0.1/x", "http://0x7f000001/x",
    "http://user:pw@10.1.1.1/x", "10.0.0.5/ohne-schema", "localhost:8000/clip.mp4",
    "http://224.0.0.1/x", "http://255.255.255.255/x",
]
for u in ABGELEHNT:
    g = downloader.url_pruefen(u)
    check(f"{u:40} abgelehnt", bool(g) and "nicht erlaubt" in g, f"-> {g!r}")
SCHEMA = ["ftp://example.invalid/a.mp4", "ftps://example.invalid/a", "file:///etc/passwd",
          "file:/etc/passwd", "rtsp://example.invalid/s", "rtmp://example.invalid/s",
          "data:text/plain,hallo", "javascript:alert(1)", "gopher://example.invalid/"]
for u in SCHEMA:
    g = downloader.url_pruefen(u)
    check(f"{u:40} nur http/https", bool(g) and "http" in g, f"-> {g!r}")
ERLAUBT = ["https://www.youtube.com/watch?v=abc", "http://example.invalid/x",
           "youtube.com/watch?v=abc", "HTTPS://Example.INVALID/x", "ytsearch5:katzen",
           "scsearch:regen", "ytsearchdate:katzen", "https://8.8.8.8/x",
           "https://[2606:4700::1111]/x", "https://100.64.0.1/x",
           "https://xn--bcher-kva.example/x", "https://example.invalid:8443/pfad?a=1#f"]
for u in ERLAUBT:
    g = downloader.url_pruefen(u)
    check(f"{u:40} erlaubt", g == "", f"-> {g!r}")
check("Leere Adresse hat einen Grund", downloader.url_pruefen("") != "")
check("Nur Schema ohne Host hat einen Grund", downloader.url_pruefen("http://") != "")
try:
    downloader.enqueue("http://192.168.1.1/x")
    check("enqueue() lehnt selbst ab (gilt auch fuer Wiederaufnahme)", False, "-> kein Fehler")
except ValueError as e:
    check("enqueue() lehnt selbst ab (gilt auch fuer Wiederaufnahme)",
          not isinstance(e, downloader.Duplikat) and "nicht erlaubt" in str(e))
check("Kein Job ist entstanden", downloader.list_jobs() == [])


print("\n── 3) Anmeldung: unbekannter Nutzer kostet dieselbe Rechenzeit ──")
aufrufe = []
_orig_hash = auth._hash_pw


def _zaehlend(pw, salt):
    aufrufe.append(salt)
    return _orig_hash(pw, salt)


auth._hash_pw = _zaehlend
check("Vor der Einrichtung gibt es keinen Admin", auth.has_admin() is False)
check("Unbekannter Nutzer -> False", auth.verify("niemand", "x") is False)
check("... aber PBKDF2 wurde trotzdem EINMAL gerechnet", len(aufrufe) == 1,
      f"-> {len(aufrufe)} Aufrufe")
auth._hash_pw = _orig_hash

# Gleichzeitige Ersteinrichtung: genau EIN Konto darf entstehen.
ergebnisse = []
startschuss = threading.Event()


def _einrichten(i):
    startschuss.wait()
    try:
        auth.setup_admin(f"admin{i}", "geheim123")
        ergebnisse.append("ok")
    except auth.SchonEingerichtet:
        ergebnisse.append("schon")
    except Exception as e:                                   # noqa: BLE001
        ergebnisse.append(f"fehler:{e}")


threads = [threading.Thread(target=_einrichten, args=(i,)) for i in range(12)]
for t in threads:
    t.start()
startschuss.set()
for t in threads:
    t.join(timeout=60)
check("12 gleichzeitige Ersteinrichtungen -> genau eine gewinnt",
      ergebnisse.count("ok") == 1 and ergebnisse.count("schon") == 11, f"-> {ergebnisse}")
check("users.json enthaelt genau EIN Konto", len(auth._users().get("users", {})) == 1,
      f'-> {list(auth._users().get("users", {}))}')
try:
    auth.setup_admin("nochmal", "geheim123")
    check("Spaetere Einrichtung wird abgewiesen", False)
except auth.SchonEingerichtet:
    check("Spaetere Einrichtung wird abgewiesen", True)
gewinner = next(iter(auth._users()["users"]))
t0 = time.perf_counter(); auth.verify(gewinner, "falsch"); bekannt = time.perf_counter() - t0
t0 = time.perf_counter(); auth.verify("gibtsnicht", "falsch"); fremd = time.perf_counter() - t0
check("Antwortzeit unbekannt ~ bekannt (kein Nutzer-Orakel)",
      fremd > bekannt * 0.3, f"-> bekannt {bekannt*1000:.0f} ms, unbekannt {fremd*1000:.0f} ms")
check("Richtiges Passwort geht weiterhin", auth.verify(gewinner, "geheim123") is True)


print("\n── 4) NAS-Freigabe: Passwort nie in der Kommandozeile ──")
MOUNT_LOG.write_text("")
res = shares.add_share("nas-medien", "192.168.1.23", "Medien", "fabian", "Ge,heim=Pass wort",
                       "WORKGROUP")
log = MOUNT_LOG.read_text(encoding="utf-8")
args_zeilen = [z for z in log.splitlines() if z.startswith("ARGS ")]
check("mount wurde aufgerufen", len(args_zeilen) == 1, f"-> {log!r}")
check("Passwort steht NICHT in den mount-Argumenten",
      "Ge,heim" not in " ".join(args_zeilen) and "password=" not in " ".join(args_zeilen))
check("Benutzername steht ebenfalls nur in der Datei",
      "username=" not in " ".join(args_zeilen))
check("Stattdessen credentials=<Datei>", "credentials=" in " ".join(args_zeilen))
check("Die Datei hatte Rechte 0600", "MODE 600" in log, f"-> {log!r}")
check("Die Datei enthielt Benutzer, Passwort (mit Komma!) und Domaene",
      "INHALT username=fabian|password=Ge,heim=Pass wort|domain=WORKGROUP|" in log,
      f"-> {log!r}")
cred = [z.split(" ", 1)[1] for z in log.splitlines() if z.startswith("CRED ")]
check("Die Datei ist danach geloescht", cred and not os.path.exists(cred[0]),
      f"-> {cred}")
check("Freigabe wurde gespeichert, Passwort verschluesselt",
      res["name"] == "nas-medien"
      and "Ge,heim" not in (config.SHARES_FILE.read_text(encoding="utf-8")))
check("vers=3.0 und die Rechte-Optionen sind geblieben",
      "vers=3.0" in args_zeilen[0] and "file_mode=0777" in args_zeilen[0])

MOUNT_LOG.write_text("")
shares._do_mount("srv", "share", "", "", "", SH / "gast")
log = MOUNT_LOG.read_text(encoding="utf-8")
check("Ohne Benutzer: guest-Option, keine credentials-Datei",
      "guest," in log and "credentials=" not in log, f"-> {log!r}")

try:
    shares._do_mount("srv", "share", "fabian", "pass\nwort", "", SH / "x")
    check("Zeilenumbruch im Passwort wird abgewiesen", False)
except ValueError:
    check("Zeilenumbruch im Passwort wird abgewiesen", True)

MOUNT_FAIL.write_text("1")
MOUNT_LOG.write_text("")
try:
    shares._do_mount("srv", "share", "fabian", "StrengGeheim99", "", SH / "kaputt")
    check("Fehlgeschlagener mount wirft", False)
    meldung = ""
except RuntimeError as e:
    meldung = str(e)
    check("Fehlgeschlagener mount wirft", True)
check("Fallback auf vers=2.0 wurde versucht",
      MOUNT_LOG.read_text(encoding="utf-8").count("ARGS ") == 2)
check("Fehlermeldung enthaelt das Passwort NICHT", "StrengGeheim99" not in meldung,
      f"-> {meldung!r}")
check("Fehlermeldung nennt die Ursache", "Permission denied" in meldung, f"-> {meldung!r}")
check("Fehlermeldung ist gekuerzt (kein dmesg-Roman)", len(meldung) <= 260,
      f"-> {len(meldung)} Zeichen")
check("credentials-Datei auch nach Fehler geloescht",
      not [z for z in MOUNT_LOG.read_text(encoding="utf-8").splitlines()
           if z.startswith("CRED ") and os.path.exists(z.split(" ", 1)[1])])
MOUNT_FAIL.unlink()
check("Filter maskiert password=... auch ohne bekannten Klartext",
      shares._fehlermeldung("bad option: username=a,password=xyz,vers=3.0")
      == "bad option: username=a,password=***,vers=3.0")
check("Leere Ausgabe -> Standardtext", shares._fehlermeldung("", None) == "mount fehlgeschlagen")


print("\n── 5) Umpacker: viel stderr darf ffmpeg nicht blockieren ──")
quelle = DL / "film.mkv"
quelle.write_bytes(b"m" * 1000)
plan = {"modus": "umpacken", "ziel": ".mp4", "spuren": {"video": "h264", "audio": "aac",
                                                          "dauer": 10.0}}


def umpacken(modus, zeitlimit=None):
    os.environ["FAKE_FFMPEG_MODE"] = modus
    ablage = playback.cache_pfad(str(quelle), ".mp4")
    playback.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ablage.unlink(missing_ok=True)
    with playback._lock:
        playback._laeuft[ablage.name] = {"fortschritt": 0, "fehler": ""}
    alt = playback._UMPACK_ZEITLIMIT
    if zeitlimit:
        playback._UMPACK_ZEITLIMIT = zeitlimit
    t = threading.Thread(target=playback._umpacken, args=(str(quelle), plan, ablage, 0),
                         daemon=True)
    t0 = time.time()
    t.start()
    t.join(timeout=25)
    playback._UMPACK_ZEITLIMIT = alt
    with playback._lock:
        stand = dict(playback._laeuft.get(ablage.name) or {})
    return ablage, t.is_alive(), time.time() - t0, stand


ablage, haengt, dauer, stand = umpacken("ok")
check("Lauf mit 400 KB stderr endet (frueher: Deadlock am Pipe-Puffer)", not haengt,
      f"-> nach {dauer:.1f} s noch aktiv")
check("Ergebnis liegt im Zwischenspeicher", ablage.is_file() and ablage.stat().st_size == 4096)
check("Laufstatus ist abgeraeumt", not stand, f"-> {stand}")
check("Keine .teil-Datei uebrig", not list(playback.CACHE_DIR.glob("*.teil")))

ablage, haengt, dauer, stand = umpacken("fail")
check("Fehlschlag endet ebenfalls", not haengt)
check("Fehlertext nennt die erste ffmpeg-Zeile (Ursache)", "kaputt" in stand.get("fehler", ""),
      f"-> {stand.get('fehler', '')[:120]!r}")
check("Fehlertext ist gekuerzt", len(stand.get("fehler", "")) <= 404)
check("Kein Ergebnis, keine .teil-Datei",
      not ablage.exists() and not list(playback.CACHE_DIR.glob("*.teil")))

ablage, haengt, dauer, stand = umpacken("hang", zeitlimit=2)
check("Haengendes ffmpeg wird vom Waechter abgeschossen", not haengt and dauer < 15,
      f"-> {dauer:.1f} s, aktiv={haengt}")
check("Fehlertext nennt den Abbruch", "abgebrochen" in stand.get("fehler", ""),
      f"-> {stand.get('fehler', '')[:120]!r}")
check("Keine .teil-Datei nach Abbruch", not list(playback.CACHE_DIR.glob("*.teil")))

print(f"\n{'='*54}\n  {ok} bestanden, {fail} fehlgeschlagen\n{'='*54}")
sys.exit(1 if fail else 0)

# ============================================================================
# Erdacht und programmiert von Fabian Hammer.
# (c) 2026 Fabian Hammer - Alle Rechte vorbehalten. Geistiges Eigentum.
# ============================================================================
"""Ablage in Unterordnern: Quelle / Art / Jahr / Monat.

Prueft die Namensfindung aus der URL, den Zusammenbau des Unterpfades, das
Festschreiben fuer die Wiederaufnahme und die Abwehr eines Pfad-Ausbruchs.
Laeuft ohne Netz, ohne ffmpeg und ohne Server -- reine Funktionspruefung.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent   # Deploy-Wurzel
ROOT = tempfile.mkdtemp(prefix="subdirs-")
CFG, DL = Path(ROOT) / "config", Path(ROOT) / "downloads"
CFG.mkdir(parents=True), DL.mkdir(parents=True)
os.environ.update(CONFIG_DIR=str(CFG), DOWNLOADS_DIR=str(DL),
                  HOSTFS_DIR=str(Path(ROOT) / "hostfs"),
                  SHARES_DIR=str(Path(ROOT) / "shares"))
sys.path.insert(0, str(BASE / "app"))

import downloader                                            # noqa: E402
from config import get_settings, save_settings               # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  \033[32mOK\033[0m   {name}")
    else:
        fail += 1
        print(f"  \033[31mFAIL\033[0m {name} {extra}")


JAHR = time.strftime("%Y")
MONAT = time.strftime("%m")

print("\n── 1) Quelle aus der URL ──")
FAELLE = [
    ("https://www.youtube.com/watch?v=abc123", "Youtube"),
    ("https://youtu.be/abc123", "Youtube"),
    ("https://m.youtube.com/watch?v=abc123", "Youtube"),
    ("https://music.youtube.com/watch?v=abc123", "YT-Musik"),
    ("https://www.youtube-nocookie.com/embed/abc", "Youtube"),
    ("youtube.com/watch?v=abc123", "Youtube"),          # ohne Schema eingefuegt
    ("HTTPS://WWW.YOUTUBE.COM/watch?v=abc", "Youtube"),  # Grossschreibung
    ("https://vimeo.com/12345", "Vimeo"),
    ("https://www.tiktok.com/@nutzer/video/1", "TikTok"),
    ("https://www.twitch.tv/videos/1", "Twitch"),
    ("https://x.com/nutzer/status/1", "X"),
    ("https://twitter.com/nutzer/status/1", "X"),
    ("https://soundcloud.com/nutzer/lied", "SoundCloud"),
    ("https://www.ardmediathek.de/video/x", "ARD"),
    ("https://www.zdf.de/filme/x", "ZDF"),
    ("https://www.arte.tv/de/videos/x", "Arte"),
    ("https://www.bbc.co.uk/programmes/x", "bbc.co.uk"),   # mehrteilige TLD
    ("https://www.gov.uk/x.mp4", "gov.uk"),               # nur drei Label
    ("https://music.youtube.com/watch?v=x", "YT-Musik"),  # eigene Rubrik
    ("https://redd.it/abc", "Reddit"),
    ("https://v.redd.it/abc", "Reddit"),
    ("https://example.org/video.mp4", "example.org"),
    ("https://cdn.beispiel-seite.de/v.mp4", "cdn.beispiel-seite.de"),
    ("https://www.beispiel-seite.de/v.mp4", "beispiel-seite.de"),
    ("http://127.0.0.1:8000/clip.mp4", "127.0.0.1"),
    ("https://nutzer:geheim@example.org/v.mp4", "example.org"),  # Zugangsdaten raus
    ("ytsearch5:katzenvideos", "Suche"),
    ("scsearch:lied", "Suche"),
    ("ytsearchall:katzen", "Suche"),        # yt-dlp kennt auch Varianten
    ("ytsearchdate:katzen", "Suche"),       # ohne Ziffer im Praefix
    ("gvsearch10:thema", "Suche"),
    ("prxseries:abc", "Suche"),          # Suchpraefix ohne das Wort "search"
    ("localhost:8000/clip.mp4", "localhost"),   # Port, keine Suchanfrage
    ("", "Sonstige"),
    ("   ", "Sonstige"),
]
for url, erwartet in FAELLE:
    got = downloader._source_name(url)
    check(f"{url[:46]!r:50} -> {erwartet}", got == erwartet, f"-> {got!r}")

print("\n── 2) Ordnernamen sind dateisystem- und SMB-tauglich ──")
VERBOTEN = set('<>:"/\\|?*')
for url, _ in FAELLE:
    n = downloader._source_name(url)
    if not (n and not (set(n) & VERBOTEN) and n == n.strip(" .")):
        check(f"Name aus {url[:40]!r} unbedenklich", False, f"-> {n!r}")
        break
else:
    check("Kein Name enthaelt verbotene Zeichen oder Rand-Punkte", True)
check("Laenge begrenzt",
      len(downloader._source_name("https://" + "a" * 300 + ".de/x")) <= 60)
check("Unsichtbare Steuerzeichen fliegen raus",
      "\u202e" not in downloader._source_name("https://\u202eevil.de/x"),
      f'-> {downloader._source_name("https://\u202eevil.de/x")!r}')
check("Reservierter Windows-Name wird entschaerft",
      downloader._source_name("https://con.com/x").lower() != "con",
      f'-> {downloader._source_name("https://con.com/x")!r}')

print("\n── 2b) Rubriken fuer den Verlauf ──")
RUB = [
    ("https://www.youtube.com/watch?v=x", "youtube"),
    ("https://music.youtube.com/watch?v=x", "musik"),
    ("https://soundcloud.com/a/b", "musik"),
    ("https://vimeo.com/1", "video"),
    ("https://www.ardmediathek.de/x", "mediathek"),
    ("https://www.tiktok.com/@a/video/1", "social"),
    ("https://www.pornhub.com/view_video.php?viewkey=x", "erwachsen"),
    ("ytsearch5:katzen", "suche"),
    ("https://irgendwas.example/x", "sonstiges"),
    ("", "sonstiges"),
]
for url, erwartet in RUB:
    got = downloader._rubrik_fuer(url)
    check(f"Rubrik {erwartet:10} fuer {url[:34]!r}", got == erwartet, f"-> {got!r}")
check("Alle Rubriken sind in der Anzeigeliste erklaert",
      all(k in downloader.RUBRIK_SCHLUESSEL for _, k in
          [(u, downloader._rubrik_fuer(u)) for u, _ in RUB]))
check("IPv6 wird normiert",
      downloader._source_name("http://[0:0:0:0:0:0:0:1]/x")
      == downloader._source_name("http://[::1]/x"),
      f'-> {downloader._source_name("http://[0:0:0:0:0:0:0:1]/x")!r} vs '
      f'{downloader._source_name("http://[::1]/x")!r}')

print("\n── 3) Unterpfad aus Quelle, Art, Jahr und Monat ──")
check("Video-Pfad",
      downloader._subdir_for("https://youtu.be/x", "best")
      == f"Youtube/video/{JAHR}/{MONAT}",
      f'-> {downloader._subdir_for("https://youtu.be/x", "best")}')
check("Audio-Pfad",
      downloader._subdir_for("https://youtu.be/x", "audio")
      == f"Youtube/audio/{JAHR}/{MONAT}")
check("Eigener Formatausdruck zaehlt als Video",
      downloader._subdir_for("https://youtu.be/x", "custom")
      == f"Youtube/video/{JAHR}/{MONAT}")
check("Monat ist zweistellig", len(MONAT) == 2 and MONAT.isdigit())

print("\n── 4) Einbau in die yt-dlp-Optionen ──")
s = save_settings({"default_dest": str(DL), "organize_subdirs": True})
check("Einstellung existiert und ist an", s.get("organize_subdirs") is True)

job = downloader._new_job("https://www.youtube.com/watch?v=abc", {"dest": str(DL)})
o = downloader._build_opts(job, s)
erwartet = str(DL / "Youtube" / "video" / JAHR / MONAT)
check("paths.home enthaelt den Unterpfad", o["paths"]["home"] == erwartet,
      f'-> {o["paths"]["home"]}')
check("Unterordner wird NICHT vorab angelegt (kein Muell bei Fehleingaben)",
      not Path(erwartet).exists())
check("Basisordner existiert weiterhin", DL.is_dir())
check("job.dest zeigt den echten Ordner", job["dest"] == erwartet)
check("Unterpfad ist festgeschrieben",
      job["overrides"].get("subdir") == f"Youtube/video/{JAHR}/{MONAT}",
      f'-> {job["overrides"].get("subdir")!r}')
check("Basisordner bleibt unveraendert", job["overrides"]["dest"] == str(DL))
check("Dateiname-Vorlage bleibt unberuehrt",
      "/" not in o["outtmpl"] and "%(title)s" in o["outtmpl"], f'-> {o["outtmpl"]}')

job_a = downloader._new_job("https://youtu.be/x", {"dest": str(DL),
                                                   "format_mode": "audio"})
o_a = downloader._build_opts(job_a, s)
check("Nur-Audio landet unter audio/",
      o_a["paths"]["home"] == str(DL / "Youtube" / "audio" / JAHR / MONAT),
      f'-> {o_a["paths"]["home"]}')

job_o = downloader._new_job("https://youtu.be/x", {})     # ohne eigenes Ziel
o_o = downloader._build_opts(job_o, s)
check("Ohne eigenes Ziel greift der Standard-Zielordner",
      o_o["paths"]["home"] == str(DL / "Youtube" / "video" / JAHR / MONAT),
      f'-> {o_o["paths"]["home"]}')
check("Basisordner wird fuer die Wiederaufnahme festgehalten",
      job_o["overrides"].get("dest") == str(DL),
      f'-> {job_o["overrides"].get("dest")!r}')

print("\n── 5) Wiederaufnahme trifft denselben Ordner ──")
alt = dict(job["overrides"])                       # so kommt es aus dem Verlauf
job2 = downloader._new_job(job["url"], alt)
o2 = downloader._build_opts(job2, s)
check("Zweiter Lauf mit alten Optionen -> gleicher Ordner",
      o2["paths"]["home"] == o["paths"]["home"],
      f'-> {o2["paths"]["home"]} statt {o["paths"]["home"]}')
alt_alt = {"dest": str(DL), "subdir": "Youtube/video/2020/01"}
job3 = downloader._new_job(job["url"], alt_alt)
o3 = downloader._build_opts(job3, s)
check("Alter Monat aus dem Verlauf wird respektiert",
      o3["paths"]["home"] == str(DL / "Youtube" / "video" / "2020" / "01"),
      f'-> {o3["paths"]["home"]}')
check("Schema wird nicht doppelt angehaengt",
      o3["paths"]["home"].count("Youtube") == 1)

print("\n── 6) Schalter aus -> Verhalten wie bisher ──")
s_aus = save_settings({"organize_subdirs": False})
check("Einstellung ist aus", s_aus.get("organize_subdirs") is False)
job4 = downloader._new_job("https://youtu.be/x", {"dest": str(DL)})
o4 = downloader._build_opts(job4, s_aus)
check("Zielordner ohne Unterpfad", o4["paths"]["home"] == str(DL),
      f'-> {o4["paths"]["home"]}')
check("Flache Ablage wird ausdruecklich festgehalten",
      job4["overrides"].get("subdir") == "",
      f'-> {job4["overrides"].get("subdir")!r}')
job5 = downloader._new_job("https://youtu.be/x",
                           {"dest": str(DL), "subdir": "Youtube/video/2020/01"})
o5 = downloader._build_opts(job5, s_aus)
check("Bestehender Verlaufseintrag wird trotzdem getroffen",
      o5["paths"]["home"] == str(DL / "Youtube" / "video" / "2020" / "01"),
      f'-> {o5["paths"]["home"]}')

print("\n── 6b) Altbestand bleibt liegen, wo er liegt ──")
s = save_settings({"organize_subdirs": True})
downloader._record({"id": "altjob", "url": "https://youtu.be/alt",
                    "title": "Alt", "filename": "alt.mp4", "dest": str(DL),
                    "status": "canceled", "error": "abgebrochen", "filesize": 5,
                    "overrides": {"dest": str(DL)},      # KEIN subdir -- Altbestand
                    "files": [], "log": [], "duration": 0})
r = downloader.retry("altjob")
check("Wiederaufnahme eines Alteintrags reiht ein", r is not None)
alt_job = downloader._jobs.get(r["id"]) if r else None
o_alt = downloader._build_opts(alt_job, s) if alt_job else {}
check("Alteintrag laedt weiter flach in den Zielordner -- sonst waere die "
      ".part-Datei verloren",
      o_alt.get("paths", {}).get("home") == str(DL),
      f'-> {o_alt.get("paths", {}).get("home")}')
downloader.cancel(r["id"])

print("\n── 6c) In der Warteschlange abgebrochene Jobs ──")
s = save_settings({"organize_subdirs": True})
j = downloader.enqueue("https://www.youtube.com/watch?v=warte", {"dest": str(DL)})
eingereiht = downloader._jobs[j["id"]]["overrides"].get("subdir")
downloader.cancel(j["id"])                    # nicht wirklich laden lassen
check("Unterpfad steht schon beim EINREIHEN fest, nicht erst beim Start",
      eingereiht == f"Youtube/video/{JAHR}/{MONAT}", f"-> {eingereiht!r}")

# Genau der Fall aus der Pruefung: in der Warteschlange abgebrochen, also nie
# durch _build_opts gelaufen -- der Eintrag traegt trotzdem seinen Unterpfad.
downloader._record({"id": "wartejob", "url": "https://www.youtube.com/watch?v=w",
                    "title": "Wartend", "filename": "", "dest": str(DL),
                    "status": "canceled", "error": "Vor dem Start abgebrochen.",
                    "filesize": 0, "files": [], "log": [], "duration": 0,
                    "overrides": {"dest": str(DL), "subdir": eingereiht}})
w = downloader.retry("wartejob")
o_w = downloader._build_opts(downloader._jobs[w["id"]], s)
check("Fortsetzen landet im Unterordner statt flach",
      o_w["paths"]["home"] == str(DL / "Youtube" / "video" / JAHR / MONAT),
      f'-> {o_w["paths"]["home"]}')
downloader.cancel(w["id"])

print("\n── 7) Kein Ausbruch aus dem Zielordner ──")
s = save_settings({"organize_subdirs": True})
AUSBRUCH = [
    "../../etc", "..", "../", "/etc/passwd", "a/../../b", "./../x",
    "..\\..\\windows", "C:\\Windows", "Youtube/../../../tmp",
    "sub\x00dir", "  ..  ", "...", "a/b/c/d/e/f/g/h/i/j/k/l",
]
schlimm = []
for boese in AUSBRUCH:
    j = downloader._new_job("https://youtu.be/x", {"dest": str(DL),
                                                    "subdir": boese})
    oo = downloader._build_opts(j, s)
    heim = Path(oo["paths"]["home"]).resolve()
    if not (heim == DL.resolve() or str(heim).startswith(str(DL.resolve()) + os.sep)):
        schlimm.append((boese, str(heim)))
check("Kein boesartiger Unterpfad fuehrt aus dem Zielordner heraus",
      not schlimm, f"-> {schlimm}")
check("Traversal-Segmente werden entfernt",
      downloader._clean_subdir("../../etc") == "",
      f'-> {downloader._clean_subdir("../../etc")!r}')
check("Absoluter Pfad wird relativ",
      downloader._clean_subdir("/etc/passwd") == "etc/passwd",
      f'-> {downloader._clean_subdir("/etc/passwd")!r}')
check("Rueckwaerts-Schraegstriche zaehlen als Trenner",
      downloader._clean_subdir("a\\b") == "a/b",
      f'-> {downloader._clean_subdir("a\\b")!r}')
check("Tiefe ist begrenzt",
      len(downloader._clean_subdir("a/b/c/d/e/f/g/h/i/j/k/l").split("/")) <= 8)
check("Platzhalter fuer Umgebungsvariablen werden entfernt",
      all(z not in downloader._clean_subdir("$HOME/~/%APPDATA%/x")
          for z in "$~%"),
      f'-> {downloader._clean_subdir("$HOME/~/%APPDATA%/x")!r}')
check("Auch im Quellnamen kein $ ~ %",
      all(z not in downloader._source_name("http://$HOME~%x%.de/v") for z in "$~%"),
      f'-> {downloader._source_name("http://$HOME~%x%.de/v")!r}')
# Gegenprobe fuer die LETZTE Sperre in _build_opts: sie greift nur, wenn
# _clean_subdir versagt. Also genau das erzwingen.
_echt = downloader._clean_subdir
downloader._clean_subdir = lambda roh: "../../ausbruch"
try:
    j_b = downloader._new_job("https://youtu.be/x", {"dest": str(DL),
                                                      "subdir": "egal"})
    o_b = downloader._build_opts(j_b, s)
    check("Auch bei versagender Vorpruefung bleibt der Pfad im Zielordner",
          o_b["paths"]["home"] == str(DL), f'-> {o_b["paths"]["home"]}')
    check("Der untaugliche Unterpfad wird dabei verworfen",
          j_b["overrides"]["subdir"] == "",
          f'-> {j_b["overrides"]["subdir"]!r}')
finally:
    downloader._clean_subdir = _echt

check("Sauberer Pfad bleibt unveraendert",
      downloader._clean_subdir(f"Youtube/video/{JAHR}/{MONAT}")
      == f"Youtube/video/{JAHR}/{MONAT}")

print("\n── 8) Der Server nimmt 'subdir' nicht vom Client entgegen ──")
import server                                                # noqa: E402
body = {"url": "http://x/y", "dest": str(DL), "subdir": "../../etc",
        "video_container": "mkv"}
ov = server._overrides_from(body)
check("'subdir' wird aus dem Request-Body verworfen", "subdir" not in ov,
      f"-> {ov}")
check("Uebrige Felder bleiben erhalten",
      ov.get("dest") == str(DL) and ov.get("video_container") == "mkv")
check("'url' und 'urls' sind weiterhin keine Optionen",
      "url" not in ov and "urls" not in server._overrides_from({"urls": ["a"]}))
check("Unbekannte Felder kommen gar nicht erst durch",
      not server._overrides_from({"paths": {"home": "/etc"}, "cookiefile": "/x",
                                  "postprocessors": []}),
      f'-> {server._overrides_from({"paths": 1, "cookiefile": 2})}')
check("Alle Optionen, die die Engine liest, sind erlaubt",
      {"dest", "format_mode", "video_container", "max_quality", "audio_format",
       "custom_format", "output_template", "playlist_items"}
      <= set(server._ERLAUBTE_OPTIONEN))
check("Dateinamen-Vorlage mit Unterordner bleibt erlaubt",
      server._tmpl_ok("%(uploader)s/%(title)s.%(ext)s"))
check("Dateinamen-Vorlage mit .. wird abgewiesen",
      not server._tmpl_ok("../%(title)s.%(ext)s")
      and not server._tmpl_ok("a/../../%(title)s.%(ext)s")
      and not server._tmpl_ok("/etc/%(title)s.%(ext)s"))
check("Leere Vorlage stoert nicht", server._tmpl_ok("") and server._tmpl_ok(None))

print(f"\n{'='*54}\n  {ok} bestanden, {fail} fehlgeschlagen\n{'='*54}")
sys.exit(1 if fail else 0)

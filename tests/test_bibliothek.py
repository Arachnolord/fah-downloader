# ============================================================================
# Erdacht und programmiert von Fabian Hammer.
# (c) 2026 Fabian Hammer - Alle Rechte vorbehalten. Geistiges Eigentum.
# ============================================================================
"""Rubriken, Zugangsdaten je Seite, Einsortieren des Altbestands, Player-Plan.

Laeuft ohne Netz und ohne Docker. Der echte ffmpeg-Lauf des Players ist hier
nicht dabei -- dafuer fehlt auf einem Mac ohne ffmpeg die Grundlage; geprueft
wird stattdessen die Entscheidung, WAS mit einer Datei passieren soll.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ROOT = tempfile.mkdtemp(prefix="bib-")
CFG, DL = Path(ROOT) / "config", Path(ROOT) / "downloads"
CFG.mkdir(parents=True), DL.mkdir(parents=True)
(CFG / "settings.json").write_text(json.dumps({"default_dest": str(DL)}))
os.environ.update(CONFIG_DIR=str(CFG), DOWNLOADS_DIR=str(DL),
                  HOSTFS_DIR=str(Path(ROOT) / "hostfs"),
                  SHARES_DIR=str(Path(ROOT) / "shares"))
sys.path.insert(0, str(BASE / "app"))

import downloader                                            # noqa: E402
import logins                                                # noqa: E402
import playback                                              # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  \033[32mOK\033[0m   {name}")
    else:
        fail += 1
        print(f"  \033[31mFAIL\033[0m {name} {extra}")


def eintrag(jid, url, dateien, wann, status="done"):
    """Einen Verlaufseintrag verbuchen, als waere er echt gelaufen."""
    downloader._record({
        "id": jid, "url": url, "title": f"Titel {jid}",
        "filename": os.path.basename(dateien[0]) if dateien else "",
        "dest": os.path.dirname(dateien[0]) if dateien else str(DL),
        "status": status, "error": "", "filesize": 10,
        "overrides": {"dest": str(DL)}, "files": dateien, "log": [],
        "duration": 0,
    })
    with downloader._db_lock:
        conn = downloader._db()
        conn.execute("UPDATE history SET finished=? WHERE id=?", (wann, jid))
        conn.commit()
        conn.close()


def datei(pfad: Path, inhalt=b"x" * 2048):
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_bytes(inhalt)
    return str(pfad)


print("\n── 1) Zugangsdaten je Seite ──")
check("Domain aus voller URL", logins.normalize_domain(
    "https://www.Beispiel.de/video/x?a=1") == "beispiel.de")
check("Domain aus blankem Host", logins.normalize_domain("beispiel.de") == "beispiel.de")
check("www. faellt weg", logins.normalize_domain("www.beispiel.de") == "beispiel.de")
check("Port faellt weg", logins.normalize_domain("beispiel.de:8080") == "beispiel.de")
check("Unsinn wird abgewiesen", logins.normalize_domain("???") == "")
check("Leereingabe wird abgewiesen", logins.normalize_domain("  ") == "")

e = logins.save_login("https://www.beispiel.de/x", "max", "geheim", note="Abo")
check("Anlegen normalisiert die Domain", e["domain"] == "beispiel.de", f"-> {e}")
check("Passwort wird als gesetzt gemeldet", e["has_password"] is True)
check("Liste enthaelt NIE das Passwort",
      all("password" not in k or k.startswith("has_")
          for k in logins.list_logins()[0]),
      f"-> {logins.list_logins()[0]}")
gespeichert = json.loads((CFG / "logins.json").read_text())
check("In der Datei steht kein Klartext-Passwort",
      "geheim" not in json.dumps(gespeichert), f"-> {gespeichert}")

check("Treffer ueber Subdomain",
      logins.login_for("https://kanal.beispiel.de/v")["password"] == "geheim")
check("Treffer ohne www", logins.login_for("http://beispiel.de/v")["username"] == "max")
check("Fremde Domain trifft nicht", logins.login_for("https://andere.de/v") == {})

logins.save_login("beispiel.de", "moritz")          # Passwort NICHT mitgeschickt
check("Leeres Passwort laesst das alte stehen",
      logins.login_for("https://beispiel.de/v")["password"] == "geheim")
check("Benutzername wurde geaendert",
      logins.login_for("https://beispiel.de/v")["username"] == "moritz")

logins.save_login("kanal.beispiel.de", "spezial", "andersrum")
check("Genauere Domain gewinnt",
      logins.login_for("https://kanal.beispiel.de/v")["username"] == "spezial")
check("Loeschen meldet Erfolg", logins.delete_login("kanal.beispiel.de") is True)
check("Loeschen eines Unbekannten meldet False",
      logins.delete_login("gibtsnicht.de") is False)
check("Nach dem Loeschen greift wieder der allgemeine Eintrag",
      logins.login_for("https://kanal.beispiel.de/v")["username"] == "moritz")

print("\n── 2) Zugangsdaten landen in den yt-dlp-Optionen ──")
from config import get_settings                              # noqa: E402
s = get_settings()
job = downloader._new_job("https://beispiel.de/video/1", {"dest": str(DL)})
o = downloader._build_opts(job, s)
check("Benutzername gesetzt", o.get("username") == "moritz", f'-> {o.get("username")}')
check("Passwort gesetzt", o.get("password") == "geheim")
check("Zugangsdaten stehen NICHT in den overrides (sonst in der Datenbank)",
      "password" not in job["overrides"] and "username" not in job["overrides"],
      f'-> {job["overrides"]}')
job2 = downloader._new_job("https://ohne-login.de/v", {"dest": str(DL)})
o2 = downloader._build_opts(job2, s)
check("Ohne Eintrag keine Anmeldedaten",
      "username" not in o2 and "password" not in o2)

print("\n── 3) Rubriken im Verlauf ──")
jetzt = int(time.time())
juni = int(time.mktime((2026, 6, 15, 12, 0, 0, 0, 0, -1)))
eintrag("j1", "https://www.youtube.com/watch?v=a", [datei(DL / "Video A.mkv")], juni)
eintrag("j2", "https://music.youtube.com/watch?v=b", [datei(DL / "Lied B.m4a")], juni)
eintrag("j3", "https://vimeo.com/3", [datei(DL / "Film C.mp4")], jetzt)
eintrag("j4", "https://nirgends.example/d", [datei(DL / "Datei D.mp4")], jetzt)
zaehler = downloader.rubrik_counts()
check("Youtube gezaehlt", zaehler.get("youtube") == 1, f"-> {zaehler}")
check("Musik gezaehlt (YT-Musik)", zaehler.get("musik") == 1, f"-> {zaehler}")
check("Video-Portale gezaehlt", zaehler.get("video") == 1, f"-> {zaehler}")
check("Unbekanntes unter Sonstiges", zaehler.get("sonstiges") == 1, f"-> {zaehler}")
check("Filter liefert nur die Rubrik",
      [h["id"] for h in downloader.history("", 50, "", "youtube")] == ["j1"],
      f'-> {[h["id"] for h in downloader.history("", 50, "", "youtube")]}')
check("Ohne Filter kommt alles", len(downloader.history("", 50)) == 4)
check("Quelle steht am Eintrag",
      downloader.history_one("j2")["quelle"] == "YT-Musik",
      f'-> {downloader.history_one("j2")["quelle"]}')

print("\n── 4) Altbestand nachtraeglich einsortieren ──")
plan = downloader.organize_plan()
nach = {m["jid"]: m["nach"] for m in plan["moves"]}
check("Alle vier fertigen Dateien im Plan", len(plan["moves"]) == 4,
      f'-> {len(plan["moves"])}')
check("Jahr und Monat kommen vom Download-Zeitpunkt, nicht von heute",
      nach.get("j1", "").endswith(str(DL / "Youtube" / "video" / "2026" / "06"
                                      / "Video A.mkv")),
      f'-> {nach.get("j1")}')
check("Tonspur landet unter audio/",
      "/YT-Musik/audio/2026/06/" in nach.get("j2", ""), f'-> {nach.get("j2")}')
check("Unbekannte Quelle behaelt ihren Host",
      "/nirgends.example/video/" in nach.get("j4", ""), f'-> {nach.get("j4")}')
check("Gesamtgroesse wird ausgewiesen", plan["gesamt_bytes"] == 4 * 2048,
      f'-> {plan["gesamt_bytes"]}')

erg = downloader.organize_run()
check("Vier Dateien verschoben", erg["verschoben"] == 4, f"-> {erg}")
check("Keine Fehler", not erg["fehler"], f'-> {erg["fehler"]}')
check("Datei liegt wirklich am neuen Ort",
      (DL / "Youtube" / "video" / "2026" / "06" / "Video A.mkv").is_file())
check("Am alten Ort liegt nichts mehr", not (DL / "Video A.mkv").exists())
rec1 = downloader.history_one("j1")
check("Verlauf zeigt auf den neuen Pfad",
      rec1["files"][0] == str(DL / "Youtube" / "video" / "2026" / "06" / "Video A.mkv"),
      f'-> {rec1["files"]}')
check("Eintrag gilt weiter als vorhanden", rec1["exists"] is True)
check("Unterpfad ist vermerkt -- Wiederaufnahme trifft den neuen Ordner",
      rec1["opts"].get("subdir") == "Youtube/video/2026/06",
      f'-> {rec1["opts"].get("subdir")}')

check("Zweiter Durchlauf findet nichts mehr",
      downloader.organize_plan()["moves"] == [],
      f'-> {downloader.organize_plan()["moves"]}')

print("\n── 5) Einsortieren ueberschreibt nie fremde Dateien ──")
opfer = DL / "Youtube" / "video" / "2026" / "06" / "Kollision.mkv"
datei(opfer, b"WICHTIG - darf nicht verloren gehen")
eintrag("j5", "https://www.youtube.com/watch?v=e", [datei(DL / "Kollision.mkv")], juni)
plan5 = downloader.organize_plan()
check("Kollision wird im Trockenlauf angezeigt",
      any(m["kollision"] for m in plan5["moves"]), f'-> {plan5["moves"]}')
erg5 = downloader.organize_run()
check("Kollision wird uebersprungen statt ueberschrieben",
      erg5["verschoben"] == 0 and erg5["uebersprungen"] == 1, f"-> {erg5}")
check("Die vorhandene Datei ist unveraendert",
      opfer.read_bytes() == b"WICHTIG - darf nicht verloren gehen")
check("Die neue Datei liegt noch an ihrem Platz", (DL / "Kollision.mkv").is_file())

print("\n── 6) Was der Player mit einer Datei vorhat ──")
for endung in (".mp4", ".webm", ".m4a", ".mp3", ".mov"):
    plan = playback.plan_fuer(str(DL / f"probe{endung}"))
    check(f"{endung:6} geht direkt an den Browser", plan["modus"] == "direkt",
          f"-> {plan}")
mkv = datei(DL / "probe.mkv")
plan_mkv = playback.plan_fuer(mkv)
check("Ohne ffmpeg gibt es einen ehrlichen Grund statt eines toten Players",
      plan_mkv["modus"] in ("umpacken", "nein") and
      (plan_mkv["modus"] == "umpacken" or plan_mkv["grund"]),
      f"-> {plan_mkv}")
check("Zwischenspeicher-Name haengt an Datei und Zielformat",
      playback.cache_pfad(mkv, ".mp4") != playback.cache_pfad(mkv, ".webm"))
check("Zwischenspeicher-Name aendert sich, wenn die Quelle sich aendert",
      (lambda vorher: (datei(DL / "probe.mkv", b"y" * 4096),
                       vorher != playback.cache_pfad(mkv, ".mp4"))[1])(
          playback.cache_pfad(mkv, ".mp4")))

playback.CACHE_DIR.mkdir(parents=True, exist_ok=True)
for i in range(3):
    p = playback.CACHE_DIR / f"alt{i}.mp4"
    p.write_bytes(b"z" * 1024)
    os.utime(p, (1000 + i, 1000 + i))          # aelteste zuerst
check("Zwischenspeicher meldet seine Groesse", playback.cache_groesse() == 3072,
      f"-> {playback.cache_groesse()}")
playback._aufraeumen(1500)
uebrig = sorted(p.name for p in playback.CACHE_DIR.glob("*.mp4"))
check("Aufraeumen wirft die aeltesten zuerst weg", uebrig == ["alt2.mp4"],
      f"-> {uebrig}")
check("Leeren gibt alles frei", playback.cache_leeren() == 1024
      and playback.cache_groesse() == 0)

print("\n── 7) Zwischenspeicher-Grenze 0 heisst unbegrenzt ──")
for i in range(3):
    p = playback.CACHE_DIR / f"neu{i}.mp4"
    p.write_bytes(b"z" * 1024)
    os.utime(p, (2000 + i, 2000 + i))
playback._aufraeumen(0)
check("Grenze 0: nichts wird geloescht (frueher flog alles raus)",
      sorted(p.name for p in playback.CACHE_DIR.glob("*.mp4"))
      == ["neu0.mp4", "neu1.mp4", "neu2.mp4"],
      f"-> {sorted(p.name for p in playback.CACHE_DIR.glob('*.mp4'))}")
playback._aufraeumen(-5)
check("Negative Grenze wird wie 0 behandelt", playback.cache_groesse() == 3072)
playback._aufraeumen(1024)
check("Eine echte Grenze raeumt weiterhin auf",
      sorted(p.name for p in playback.CACHE_DIR.glob("*.mp4")) == ["neu2.mp4"])
playback.cache_leeren()

print(f"\n{'='*54}\n  {ok} bestanden, {fail} fehlgeschlagen\n{'='*54}")
sys.exit(1 if fail else 0)

# ============================================================================
# Erdacht und programmiert von Fabian Hammer.
# (c) 2026 Fabian Hammer - Alle Rechte vorbehalten. Geistiges Eigentum.
# ============================================================================
"""Welche Release-ZIP gewinnt? Die mit der hoechsten (N) -- nicht die juengste.

Der Browser zaehlt beim erneuten Herunterladen hoch: "fah-downloader (1).zip"
ist NEUER als "fah-downloader.zip", auch wenn deren Aenderungszeit spaeter
liegt (kopiert, angefasst, aus einer Sicherung zurueckgeholt).
"""
import os
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "tools"))

import release_zip                                           # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  \033[32mOK\033[0m   {name}")
    else:
        fail += 1
        print(f"  \033[31mFAIL\033[0m {name} {extra}")


def ordner(*dateien):
    """Ein Verzeichnis mit ZIPs; (name, alter_in_sekunden) -- aelter = frueher."""
    d = tempfile.mkdtemp(prefix="zips-")
    jetzt = time.time()
    for name, alter in dateien:
        p = os.path.join(d, name)
        Path(p).write_bytes(b"PK")
        os.utime(p, (jetzt - alter, jetzt - alter))
    return d


def name(p):
    return os.path.basename(p) if p else None


print("\n── 1) Namensmuster ──")
B = "fah-downloader.zip"
for datei, erwartet in [
    ("fah-downloader.zip", 0),
    ("fah-downloader (1).zip", 1),
    ("fah-downloader (2).zip", 2),
    ("fah-downloader(3).zip", 3),           # ohne Leerzeichen
    ("fah-downloader (12).zip", 12),
    ("fah-downloader (3) (1).zip", 4),      # zweimal nummeriert -> nach (3)
    ("FAH-Downloader (1).ZIP", 1),          # Gross-/Kleinschreibung egal
    ("fah-downloader-2.4.1.zip", None),     # anderes Schema -> nicht dabei
    ("fah-downloader.zip.bak", None),
    ("fah-downloaderX.zip", None),
    ("alt-fah-downloader.zip", None),
]:
    got = release_zip.nummer(datei, B)
    check(f"{datei:32} -> {erwartet}", got == erwartet, f"-> {got}")

print("\n── 2) Hoechste Nummer gewinnt -- auch gegen eine juengere Datei ──")
d = ordner(("fah-downloader.zip", 10),          # frisch angefasst
           ("fah-downloader (1).zip", 3600))     # eine Stunde alt
check("(1) schlaegt die nackte Datei trotz aelterer Zeit",
      name(release_zip.neueste(d, B)) == "fah-downloader (1).zip",
      f"-> {name(release_zip.neueste(d, B))}")

d = ordner(("fah-downloader.zip", 1),
           ("fah-downloader (1).zip", 7200),
           ("fah-downloader (2).zip", 3600))
check("(2) schlaegt (1) und die nackte Datei",
      name(release_zip.neueste(d, B)) == "fah-downloader (2).zip")
check("Liste ist von neu nach alt sortiert",
      [name(p) for p in release_zip.kandidaten(d, B)]
      == ["fah-downloader (2).zip", "fah-downloader (1).zip", "fah-downloader.zip"],
      f"-> {[name(p) for p in release_zip.kandidaten(d, B)]}")

d = ordner(("fah-downloader (10).zip", 100), ("fah-downloader (9).zip", 10))
check("Zweistellig wird numerisch verglichen, nicht als Text",
      name(release_zip.neueste(d, B)) == "fah-downloader (10).zip")

print("\n── 3) Gleiche Nummer: dann entscheidet die Zeit ──")
d = ordner(("fah-downloader.zip", 5))
check("Einzige Datei wird gefunden", name(release_zip.neueste(d, B)) == "fah-downloader.zip")
d = ordner(("fah-downloader (3).zip", 100), ("fah-downloader(3).zip", 5))
check("Bei gleicher Nummer gewinnt die juengere",
      name(release_zip.neueste(d, B)) == "fah-downloader(3).zip")

print("\n── 4) Fremde Dateien stoeren nicht ──")
d = ordner(("fah-downloader (1).zip", 10),
           ("fah-converter (5).zip", 1),           # anderes Projekt!
           ("fah-downloader-2.4.1.zip", 1),
           ("downloader_release.zip", 1),
           ("notizen.txt", 1))
check("Nur das eigene Namensschema zaehlt",
      name(release_zip.neueste(d, B)) == "fah-downloader (1).zip",
      f"-> {name(release_zip.neueste(d, B))}")
check("Leerer Ordner -> nichts", release_zip.neueste(tempfile.mkdtemp(), B) is None)
check("Fehlender Ordner -> nichts, kein Absturz",
      release_zip.neueste("/gibt/es/nicht", B) is None)

print("\n── 5) Aufraeumen nach dem Einspielen ──")
d = ordner(("fah-downloader.zip", 300),
           ("fah-downloader (1).zip", 200),
           ("fah-downloader (2).zip", 100),
           ("fah-converter (1).zip", 50))
sieger = release_zip.neueste(d, B)
weg = release_zip.aufraeumen(d, B, sieger)
check("Aeltere Fassungen sind weg", sorted(name(p) for p in weg)
      == ["fah-downloader (1).zip", "fah-downloader.zip"], f"-> {[name(p) for p in weg]}")
check("Die eingespielte bleibt", os.path.isfile(sieger))
check("Fremdes Projekt bleibt unangetastet",
      os.path.isfile(os.path.join(d, "fah-converter (1).zip")))
check("Danach ist die eingespielte die einzige",
      [name(p) for p in release_zip.kandidaten(d, B)] == ["fah-downloader (2).zip"])

print("\n── 6) Kommandozeile, so wie manage.sh es aufruft ──")
import subprocess                                            # noqa: E402
d = ordner(("fah-downloader.zip", 10), ("fah-downloader (1).zip", 3600))
r = subprocess.run([sys.executable, str(BASE / "tools" / "release_zip.py"),
                    "latest", d, B], capture_output=True, text=True)
check("'latest' liefert genau einen Pfad",
      r.returncode == 0 and name(r.stdout.strip()) == "fah-downloader (1).zip",
      f"-> {r.returncode} {r.stdout!r}")
r = subprocess.run([sys.executable, str(BASE / "tools" / "release_zip.py"),
                    "list", d, B], capture_output=True, text=True)
check("'list' liefert beide, neueste zuerst",
      [name(z) for z in r.stdout.split("\n") if z]
      == ["fah-downloader (1).zip", "fah-downloader.zip"])
r = subprocess.run([sys.executable, str(BASE / "tools" / "release_zip.py"),
                    "latest", tempfile.mkdtemp(), B], capture_output=True, text=True)
check("Ohne Treffer: leer und Rueckgabe 0 (manage.sh prueft selbst)",
      r.returncode == 0 and r.stdout.strip() == "")

print("\n── 7) Gegenprobe: die ALTE Regel (Zeit zuerst) waere falsch ──")
d = ordner(("fah-downloader.zip", 10), ("fah-downloader (1).zip", 3600))
alt_regel = max(release_zip.kandidaten(d, B), key=os.path.getmtime)
check("Nach Zeit wuerde die falsche gewinnen -- genau das ist behoben",
      name(alt_regel) == "fah-downloader.zip"
      and name(release_zip.neueste(d, B)) == "fah-downloader (1).zip")

print("\n── 8) manage.sh im Ganzen -- mit nachgebautem docker ──")
# Ein Deploy-Ordner wie auf dem NAS, ein docker, das nur mitschreibt.
import shutil                                                # noqa: E402
import zipfile                                               # noqa: E402
werk = Path(tempfile.mkdtemp(prefix="deploy-"))
fake_bin = werk / "bin"; fake_bin.mkdir()
protokoll = werk / "docker.log"
(fake_bin / "docker").write_text(
    "#!/bin/sh\n"
    f"echo \"$@\" >> {protokoll}\n"
    'case "$1" in\n'
    '  compose) [ "$2" = "version" ] && exit 0 ;;\n'
    '  ps) exit 0 ;;\n'
    'esac\nexit 0\n', encoding="utf-8")
(fake_bin / "docker").chmod(0o755)
(fake_bin / "docker-compose").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
(fake_bin / "docker-compose").chmod(0o755)

def release(pfad, marke):
    """Eine echte Release-ZIP aus dem Projekt bauen, mit einer Markierung."""
    with zipfile.ZipFile(pfad, "w") as z:
        for f in ("manage.sh", "Dockerfile", "docker-entrypoint.sh", ".env.example",
                  "docker-compose.yml.example", "tools/release_zip.py",
                  "tools/port_check.py", "app/version.py"):
            z.write(BASE / f, f)
        z.writestr("app/MARKE.txt", marke)

alt_zip = werk / "fah-downloader.zip"
neu_zip = werk / "fah-downloader (1).zip"
release(alt_zip, "ALT")
release(neu_zip, "NEU")
os.utime(alt_zip, (time.time(), time.time()))            # nackte Datei ist juenger
os.utime(neu_zip, (time.time() - 3600, time.time() - 3600))
shutil.copy(BASE / "manage.sh", werk / "manage.sh")
(werk / "manage.sh").chmod(0o755)

r = subprocess.run(["sh", "-c", f'PATH="{fake_bin}:$PATH" ./manage.sh update'],
                   cwd=werk, capture_output=True, text=True, timeout=120)
ausgabe = r.stdout + r.stderr
check("manage.sh update laeuft durch", r.returncode == 0, f"-> {r.returncode}\n{ausgabe[-800:]}")
check("Es wurde die (1)-Datei eingespielt, nicht die juengere nackte",
      (werk / "app" / "MARKE.txt").read_text() == "NEU",
      f'-> {(werk / "app" / "MARKE.txt").read_text() if (werk / "app" / "MARKE.txt").exists() else "fehlt"}')
log = protokoll.read_text() if protokoll.exists() else ""
check("Container wurde mit --force-recreate neu aufgebaut",
      "--force-recreate" in log, f"-> {log!r}")
check("Die eingespielte ZIP bleibt liegen", neu_zip.is_file())
check("Die aeltere Fassung wurde weggeraeumt", not alt_zip.exists(),
      f"-> {sorted(p.name for p in werk.glob('*.zip'))}")
check("tools/ liegt danach neben manage.sh", (werk / "tools" / "release_zip.py").is_file())

# Ein zweiter Aufruf ohne neue ZIP darf nichts Neues finden -- sonst deployt
# der watch-Modus dieselbe Datei in Endlosschleife.
r2 = subprocess.run(["sh", "-c", f'PATH="{fake_bin}:$PATH" ./manage.sh update'],
                    cwd=werk, capture_output=True, text=True, timeout=120)
check("Zweiter Aufruf spielt dieselbe (1)-Datei erneut ein (idempotent, kein Fehler)",
      r2.returncode == 0 and (werk / "app" / "MARKE.txt").read_text() == "NEU")

# Vom Nutzer ausdruecklich angegebene Datei: gewinnt IMMER, und bleibt.
extra = werk / "fah-downloader-eigene.zip"
release(extra, "EIGENE")
r3 = subprocess.run(["sh", "-c", f'PATH="{fake_bin}:$PATH" ./manage.sh update "{extra}"'],
                    cwd=werk, capture_output=True, text=True, timeout=120)
check("Ausdruecklich angegebene ZIP wird genommen",
      r3.returncode == 0 and (werk / "app" / "MARKE.txt").read_text() == "EIGENE",
      f"-> {r3.returncode} {(werk / 'app' / 'MARKE.txt').read_text()}")
check("Und sie wird beim Aufraeumen NICHT geloescht", extra.is_file())

print(f"\n{'='*54}\n  {ok} bestanden, {fail} fehlgeschlagen\n{'='*54}")
sys.exit(1 if fail else 0)

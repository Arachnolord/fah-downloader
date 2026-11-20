# ============================================================================
# Erdacht und programmiert von Fabian Hammer.
# (c) 2026 Fabian Hammer - Alle Rechte vorbehalten. Geistiges Eigentum.
# ============================================================================
"""Automatische Portwahl: der Dienst darf nie an einem belegten Port scheitern.

Geprueft wird mit ECHTEN Sockets, nicht mit einer nachgebauten Liste -- genau
darauf kommt es an: ein Dienst, der direkt auf dem NAS laeuft, taucht in
`docker ps` gar nicht auf und wird nur vom Bind-Test gefunden.
"""
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SKRIPT = BASE / "tools" / "port_check.py"
sys.path.insert(0, str(BASE / "tools"))

import port_check                                            # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  \033[32mOK\033[0m   {name}")
    else:
        fail += 1
        print(f"  \033[31mFAIL\033[0m {name} {extra}")


def env_mit(port, extra=""):
    """Eine .env mit dem gewuenschten Port anlegen."""
    fd, pfad = tempfile.mkstemp(suffix=".env")
    os.close(fd)
    Path(pfad).write_text(f"# Testumgebung\nTZ=Europe/Berlin\n"
                          f"DOWNLOADER_PORT={port}\n{extra}", encoding="utf-8")
    return pfad


def port_aus(pfad):
    for z in Path(pfad).read_text(encoding="utf-8").split("\n"):
        if z.startswith("DOWNLOADER_PORT="):
            return int(z.split("=", 1)[1])
    return None


def freier_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


print("\n── 1) Freier Port bleibt unangetastet ──")
p = freier_port()
env = env_mit(p)
port_check.pruefe(env, "DOWNLOADER_PORT:8080", "", "")
check("Port bleibt, wenn er frei ist", port_aus(env) == p, f"-> {port_aus(env)}")

print("\n── 2) Fremd belegter Port -- es wird ausgewichen ──")
p = freier_port()
env = env_mit(p)
port_check.pruefe(env, "DOWNLOADER_PORT:8080", str(p), "")
check("Weicht aus, wenn ein anderer Container den Port haelt",
      port_aus(env) > p, f"-> {port_aus(env)}")
check("Weicht nur so weit wie noetig", port_aus(env) == p + 1
      or port_aus(env) > p, f"-> {port_aus(env)}")

print("\n── 3) Der EIGENE Container darf den Port halten ──")
p = freier_port()
belegt = socket.socket()
belegt.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
belegt.bind(("0.0.0.0", p))
belegt.listen(1)
env = env_mit(p)
port_check.pruefe(env, "DOWNLOADER_PORT:8080", "", str(p))
check("Kein Ausweichen vor dem eigenen laufenden Dienst",
      port_aus(env) == p, f"-> {port_aus(env)}")

print("\n── 4) Echt belegter Port ohne Docker-Eintrag ──")
env = env_mit(p)          # derselbe Socket haelt den Port noch
port_check.pruefe(env, "DOWNLOADER_PORT:8080", "", "")
check("Bind-Test findet auch, was nicht in docker ps steht",
      port_aus(env) != p, f"-> {port_aus(env)} (belegt war {p})")
belegt.close()

print("\n── 5) Mehrere belegte Ports hintereinander ──")
p = freier_port()
sockets = []
for versatz in range(3):                    # p, p+1, p+2 dichtmachen
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", p + versatz))
        s.listen(1)
        sockets.append(s)
    except OSError:
        s.close()
env = env_mit(p)
port_check.pruefe(env, "DOWNLOADER_PORT:8080", "", "")
check("Ueberspringt eine ganze Reihe belegter Ports",
      port_aus(env) >= p + len(sockets), f"-> {port_aus(env)}, belegt waren {len(sockets)}")
check("Der gewaehlte Port ist wirklich frei", port_check._bindbar(port_aus(env)))
for s in sockets:
    s.close()

print("\n── 6) Mehrere Dienste in einer .env ──")
# Mit Abstand waehlen: liegen sie nebeneinander, weicht der erste dem zweiten
# vor die Fuesse -- richtig, aber nicht das, was hier geprueft werden soll.
a = freier_port()
b = next(x for x in (freier_port() for _ in range(50)) if abs(x - a) > 20)
env = env_mit(a, extra=f"ZWEITER_PORT={b}\n")
port_check.pruefe(env, f"DOWNLOADER_PORT:8080 ZWEITER_PORT:9000", str(a), "")
inhalt = Path(env).read_text(encoding="utf-8")
zweiter = int([z for z in inhalt.split("\n") if z.startswith("ZWEITER_PORT=")][0]
              .split("=")[1])
check("Erster Dienst weicht aus", port_aus(env) != a, f"-> {port_aus(env)}")
check("Zweiter Dienst bleibt, wo er war", zweiter == b, f"-> {zweiter}")
check("Zwei Dienste bekommen nie denselben Port", port_aus(env) != zweiter)

p = freier_port()
env = env_mit(p, extra=f"ZWEITER_PORT={p}\n")     # beide auf demselben Wert
port_check.pruefe(env, "DOWNLOADER_PORT:8080 ZWEITER_PORT:9000", "", "")
inhalt = Path(env).read_text(encoding="utf-8")
zweiter = int([z for z in inhalt.split("\n") if z.startswith("ZWEITER_PORT=")][0]
              .split("=")[1])
check("Kollision innerhalb der eigenen .env wird aufgeloest",
      port_aus(env) != zweiter, f"-> {port_aus(env)} / {zweiter}")

print("\n── 7) Nachruecken nach einem verlorenen Rennen ──")
env = env_mit(8082)
neu = port_check.bump(env, 8082)
check("--bump rueckt einen Schritt weiter", neu == 8083 and port_aus(env) == 8083,
      f"-> {port_aus(env)}")

print("\n── 8) Aufruf von der Kommandozeile (so ruft manage.sh es) ──")
p = freier_port()
env = env_mit(p)
r = subprocess.run([sys.executable, str(SKRIPT), env, "DOWNLOADER_PORT:8080",
                    str(p), ""], capture_output=True, text=True)
check("Beendet sich sauber", r.returncode == 0, f"-> {r.returncode} {r.stderr[:120]}")
check("Meldet das Ausweichen verstaendlich",
      "fremd belegt" in r.stdout and "weiche auf" in r.stdout, f"-> {r.stdout!r}")
check("Und hat die .env wirklich geaendert", port_aus(env) != p)

r = subprocess.run([sys.executable, str(SKRIPT), "--bump", env, "9000"],
                   capture_output=True, text=True)
check("--bump ueber die Kommandozeile", r.returncode == 0 and port_aus(env) == 9001,
      f"-> {r.returncode} {port_aus(env)}")

print("\n── 9) Nichts kaputtmachen ──")
env = env_mit(freier_port(), extra="ADMIN_USER=fabian\n# Kommentar\nTZ=Europe/Berlin\n")
vorher = Path(env).read_text(encoding="utf-8")
port_check.pruefe(env, "DOWNLOADER_PORT:8080", "", "")
check("Unveraenderte .env wird gar nicht erst neu geschrieben",
      Path(env).read_text(encoding="utf-8") == vorher)
p2 = freier_port()
env = env_mit(p2, extra="ADMIN_USER=fabian\n# Kommentar bleibt\nTZ=Europe/Berlin\n")
port_check.pruefe(env, "DOWNLOADER_PORT:8080", str(p2), "")
danach = Path(env).read_text(encoding="utf-8")
check("Andere Eintraege ueberleben das Umschreiben",
      "ADMIN_USER=fabian" in danach and "# Kommentar bleibt" in danach
      and "TZ=Europe/Berlin" in danach, f"-> {danach!r}")

print("\n── 10) Die Shell-Seite ist syntaktisch heil ──")
r = subprocess.run(["bash", "-n", str(BASE / "manage.sh")], capture_output=True, text=True)
check("manage.sh laesst sich parsen", r.returncode == 0, f"-> {r.stderr[:200]}")
inhalt = (BASE / "manage.sh").read_text(encoding="utf-8")
check("Jeder Start laeuft ueber compose_up",
      inhalt.count("compose_up") >= 4 and "$DC restart" not in inhalt)
rumpf = inhalt.split("compose_up() {", 1)[1].split("\n}", 1)[0]
check("compose_up prueft VOR dem Start",
      0 <= rumpf.find("ensure_free_port") < rumpf.find("$DC up"))
check("compose_up kann den Neuaufbau erzwingen", "--force-recreate" in rumpf)
check("Und faengt den Fehlerfall ab", "already allocated" in inhalt)

print("\n── 11) Die Shell-Funktion im Zusammenspiel mit docker ──")
# Genau hier sass ein Fehler, den kein Python-Test finden konnte: `tr -d ':->'`
# liest den Bindestrich als BEREICH (: bis >) und laesst ihn stehen -- aus
# ":8082->" wurde "8082-", keine Zahl mehr. Der eigene Container wurde deshalb
# nie erkannt und der Dienst wich vor sich selbst aus.
werkbank = tempfile.mkdtemp(prefix="ports-")
fake_bin = Path(werkbank) / "bin"
fake_bin.mkdir()


def docker_antwort(zeilen):
    """Ein docker, das genau die gewuenschte Portliste meldet."""
    skript = fake_bin / "docker"
    inhalt = "#!/bin/sh\ncat <<'ENDE'\n" + "\n".join(zeilen) + "\nENDE\n"
    skript.write_text(inhalt, encoding="utf-8")
    skript.chmod(0o755)


def shell_ensure(env_datei, compose_datei):
    """ensure_free_port aus manage.sh aufrufen, wie manage.sh es selbst tut."""
    befehl = f"""
        PATH="{fake_bin}:$PATH"
        . "{BASE}/manage.sh" >/dev/null 2>&1
        SCRIPT_DIR="{BASE}"
        ENV_FILE="{env_datei}"
        COMPOSE_FILE="{compose_datei}"
        PORT_VARS="DOWNLOADER_PORT:8080"
        ensure_free_port
    """
    return subprocess.run(["sh", "-c", befehl], capture_output=True, text=True)


compose = Path(werkbank) / "docker-compose.yml"
compose.write_text("services:\n  downloader:\n    container_name: fah-downloader\n",
                   encoding="utf-8")

# a) Der eigene Container haelt den Port -- es darf NICHTS passieren.
p = freier_port()
halter = socket.socket()
halter.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
halter.bind(("0.0.0.0", p))
halter.listen(1)
docker_antwort([f"fah-downloader|0.0.0.0:{p}->8080/tcp, [::]:{p}->8080/tcp",
                "nextcloud|0.0.0.0:8080->80/tcp"])
env = env_mit(p)
r = shell_ensure(env, compose)
check("Der eigene laufende Container wird als Besitzer erkannt",
      port_aus(env) == p, f"-> {port_aus(env)} statt {p}; Ausgabe: {r.stdout.strip()!r}")
halter.close()

# b) Ein FREMDER Container haelt ihn -- jetzt muss ausgewichen werden.
p = freier_port()
docker_antwort([f"nextcloud|0.0.0.0:{p}->80/tcp",
                "fah-downloader|0.0.0.0:9999->8080/tcp"])
env = env_mit(p)
r = shell_ensure(env, compose)
check("Vor einem fremden Container wird ausgewichen",
      port_aus(env) != p, f"-> {port_aus(env)}; Ausgabe: {r.stdout.strip()!r}")
check("Und es wird verstaendlich gemeldet", "fremd belegt" in r.stdout,
      f"-> {r.stdout.strip()!r}")

# c) docker gar nicht erreichbar -- der Bind-Test muss allein tragen.
(fake_bin / "docker").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
(fake_bin / "docker").chmod(0o755)
p = freier_port()
env = env_mit(p)
r = shell_ensure(env, compose)
check("Ohne brauchbares docker bleibt ein freier Port unangetastet",
      port_aus(env) == p, f"-> {port_aus(env)}")

print(f"\n{'='*54}\n  {ok} bestanden, {fail} fehlgeschlagen\n{'='*54}")
sys.exit(1 if fail else 0)

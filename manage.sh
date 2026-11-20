#!/bin/sh
# ============================================================================
# Erdacht und programmiert von Fabian Hammer.
# (c) 2026 Fabian Hammer - Alle Rechte vorbehalten. Geistiges Eigentum.
# Unbefugte Vervielfaeltigung, Weitergabe oder Verwertung dieses Quellcodes ohne
# schriftliche Genehmigung des Urhebers ist untersagt.
# ============================================================================
#
# manage.sh – FAH Media Downloader Management
# Kein 'unzip' noetig – funktioniert auf UGREEN/Synology NAS mit python3.
#
# Verwendung:
#   ./manage.sh update            – Update aus fah-downloader.zip (hoechste (N) gewinnt)
#   ./manage.sh update <file>     – Update aus bestimmter ZIP
#   ./manage.sh update --no-cache – Update mit vollstaendigem Rebuild
#   ./manage.sh watch             – Auf neue ZIP warten und automatisch deployen
#   ./manage.sh start|stop|restart– Container steuern
#   ./manage.sh status            – Status anzeigen
#   ./manage.sh logs              – Logs folgen
#   ./manage.sh port              – Aktuellen WebGUI-Port anzeigen
#   ./manage.sh port <PORT>       – WebGUI-Port aendern (+ Neustart)
#   ./manage.sh url               – Erreichbare WebGUI-URL anzeigen
#
# Prinzip:
#   Code-Dateien (app/, Dockerfile, entrypoint) -> bei jedem Update ersetzt.
#   Configs (.env, docker-compose.yml)          -> NIE in der ZIP; kommen als
#                                                   *.example, werden nur beim
#                                                   ersten Mal erzeugt.
#   ./config und ./downloads                     -> Nutzerdaten, NIE angetastet.
#   docker-compose.yml                           -> additiv ergaenzt, bestehende
#                                                   (angepasste) Werte bleiben.
#   Port                                         -> Erstinstallation: freier Port
#                                                   per TCP-Bind-Test; danach fix
#                                                   (Lock-Datei), Self-Update
#                                                   laesst ihn unberuehrt.

set -e
export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Release-Datei. Browser haengen beim erneuten Herunterladen " (1)", " (2)" an
# -- die HOECHSTE Nummer ist der juengste Download und gewinnt, unabhaengig
# von der Aenderungszeit. Die Auswahl steckt in tools/release_zip.py.
AUTO_ZIP_BASENAME="fah-downloader.zip"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_EXAMPLE="$SCRIPT_DIR/.env.example"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
COMPOSE_EXAMPLE="$SCRIPT_DIR/docker-compose.yml.example"
PORTS_LOCK_FILE="$SCRIPT_DIR/.downloader_ports.lock"
WATCH_INTERVAL=20
NO_CACHE="${NO_CACHE:-0}"

# Scanbare Ports als "NAME:DEFAULT". Nur der WebGUI-Port.
PORT_VARS="DOWNLOADER_PORT:8080"

# ── Docker-Compose-Kommando erkennen (v1 'docker-compose' vs v2 'docker compose')
detect_compose() {
    if docker compose version >/dev/null 2>&1; then
        DC="docker compose"
    elif command -v docker-compose >/dev/null 2>&1; then
        DC="docker-compose"
    else
        echo "Fehler: weder 'docker compose' noch 'docker-compose' gefunden."
        exit 1
    fi
}

# ── ZIP-Auswahl: hoechste (N) gewinnt, dann juengste Zeit ────────────────────
# Beim ALLERERSTEN Aufruf liegt tools/ noch nicht neben manage.sh -- dann wird
# das Modul direkt aus der ZIP gelesen, damit die Auswahl von Anfang an gilt.
_release_zip_py() {
    if [ -f "$SCRIPT_DIR/tools/release_zip.py" ]; then
        echo "$SCRIPT_DIR/tools/release_zip.py"; return 0
    fi
    for z in "$SCRIPT_DIR"/*.zip; do
        [ -f "$z" ] || continue
        python3 - "$z" "$SCRIPT_DIR/.release_zip.py" <<'PYEOF' && { echo "$SCRIPT_DIR/.release_zip.py"; return 0; }
import sys, zipfile
z, out = sys.argv[1], sys.argv[2]
try:
    with zipfile.ZipFile(z) as f:
        for n in f.namelist():
            if n.endswith("tools/release_zip.py"):
                open(out, "wb").write(f.read(n)); sys.exit(0)
except Exception:
    pass
sys.exit(1)
PYEOF
    done
    return 1
}

find_latest_zip() {
    RZ="$(_release_zip_py)" || return 0
    python3 "$RZ" latest "$SCRIPT_DIR" "$AUTO_ZIP_BASENAME" 2>/dev/null
}

# ── manage.sh nur aus ZIP extrahieren (fuer Self-Update) ─────────────────────
_extract_manage_from_zip() {
    python3 - "$1" "$2" <<'PYINNER'
import sys, zipfile, shutil, os
zip_path, out = sys.argv[1], sys.argv[2]
try:
    with zipfile.ZipFile(zip_path) as z:
        cand = None
        for n in z.namelist():
            norm = n.lstrip('/')
            if norm == 'manage.sh' or norm.endswith('/manage.sh'):
                cand = n; break
        if cand is None:
            sys.exit(0)
        with z.open(cand) as src, open(out, 'wb') as dst:
            shutil.copyfileobj(src, dst)
except Exception as e:
    sys.stderr.write("[SELF-UPDATE] konnte manage.sh nicht entpacken: %s\n" % e)
    if os.path.exists(out):
        try: os.remove(out)
        except OSError: pass
PYINNER
}

# ── Freien Port per echtem TCP-Bind-Test vergeben (nur Erstinstallation) ─────
scan_and_assign_ports() {
    [ -f "$ENV_FILE" ] || return 0
    if [ -f "$PORTS_LOCK_FILE" ]; then
        echo "  [PORTS] Lock vorhanden -- Self-Update, Port bleibt unveraendert."
        return 0
    fi
    echo "  [PORTS] Erstinstallation -- suche freien Port ..."

    # Ports bereits laufender Docker-Stacks respektieren (auch gestoppte).
    RESERVED=""
    if command -v docker >/dev/null 2>&1; then
        RESERVED="$(docker ps -a --format '{{.Ports}}' 2>/dev/null \
            | grep -oE ':[0-9]{2,5}->' | grep -oE '[0-9]+' | sort -un | tr '\n' ' ')"
    fi

    python3 - "$ENV_FILE" "$PORT_VARS" "$RESERVED" <<'PYEOF'
import sys, socket, re
env_file, port_vars_raw = sys.argv[1], sys.argv[2]
reserved_raw = sys.argv[3] if len(sys.argv) > 3 else ""
pairs = [p.split(':') for p in port_vars_raw.split()]

def is_free(port, used):
    if port in used: return False
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try: s.bind(('0.0.0.0', port))
    except OSError: return False
    finally: s.close()
    return True

used = set()
for tok in reserved_raw.split():
    try: used.add(int(tok))
    except ValueError: pass

text = open(env_file, encoding='utf-8').read()
current = {}
for l in text.split('\n'):
    m = re.match(r'^\s*([A-Za-z0-9_]+)\s*=\s*(\d{1,5})\s*$', l)
    if m: current[m.group(1)] = int(m.group(2))

lines = text.split('\n')
for name, default in pairs:
    start = current.get(name, int(default))
    port = start
    while not is_free(port, used):
        port += 1
    used.add(port)
    if port != start:
        print(f"  [PORTS] {name}: {start} belegt -> {port}")
    else:
        print(f"  [PORTS] {name}: {port} frei")
    found = False
    for i, l in enumerate(lines):
        if re.match(rf'^\s*{name}\s*=', l):
            lines[i] = f"{name}={port}"; found = True; break
    if not found:
        lines.append(f"{name}={port}")
open(env_file, 'w', encoding='utf-8').write('\n'.join(lines))
PYEOF
    touch "$PORTS_LOCK_FILE"
    echo "  [PORTS] Port fixiert (.downloader_ports.lock)."
}

# ── Vor JEDEM Start pruefen, ob der Port noch uns gehoert ───────────────────
# scan_and_assign_ports() laeuft nur einmal bei der Erstinstallation, damit die
# URL stabil bleibt. Das allein reicht nicht: Ist der Container gestoppt und ein
# anderer Dienst greift sich in der Zwischenzeit den Port, scheitert der
# naechste Start mit "port is already allocated". Diese Funktion faengt das ab.
# Sie weicht NUR aus, wenn der Port wirklich fremd belegt ist, und schreibt den
# neuen Wert in die .env.
ensure_free_port() {
    [ -f "$ENV_FILE" ] || return 0
    OWN_NAME="$(grep -oE 'container_name:[[:space:]]*[A-Za-z0-9_.-]+' "$COMPOSE_FILE" 2>/dev/null \
        | head -1 | awk '{print $2}')"
    [ -n "$OWN_NAME" ] || OWN_NAME="fah-downloader"

    # Ports anderer Container. Der eigene bleibt ausdruecklich aussen vor --
    # sonst wuerde die Automatik vor dem eigenen laufenden Dienst fliehen.
    # MERKE: die Zahl mit grep herausholen, nicht mit `tr -d ':->'`. tr liest
    # den Bindestrich zwischen : und > als BEREICH (: bis >) und laesst ihn
    # selbst stehen -- aus ":8082->" wurde "8082-", und das ist keine Zahl mehr.
    FOREIGN_PORTS=""
    OWN_PORTS=""
    if command -v docker >/dev/null 2>&1; then
        ALL_MAPS="$(docker ps -a --format '{{.Names}}|{{.Ports}}' 2>/dev/null)"
        FOREIGN_PORTS="$(printf '%s\n' "$ALL_MAPS" | grep -v "^${OWN_NAME}|" \
            | grep -oE ':[0-9]{2,5}->' | grep -oE '[0-9]+' | sort -un | tr '\n' ' ')"
        OWN_PORTS="$(printf '%s\n' "$ALL_MAPS" | grep "^${OWN_NAME}|" \
            | grep -oE ':[0-9]{2,5}->' | grep -oE '[0-9]+' | sort -un | tr '\n' ' ')"
    fi

    python3 "$SCRIPT_DIR/tools/port_check.py" "$ENV_FILE" "$PORT_VARS" \
        "$FOREIGN_PORTS" "$OWN_PORTS" 2>/dev/null || true
}

# ── Starten und bei einem Port-Rennen genau einmal neu waehlen ───────────────
# Zwischen Pruefung und "up" koennen Sekunden liegen. Schnappt sich in dieser
# Luecke ein anderer Dienst den Port, faengt das hier den Fehler ab, statt den
# Start scheitern zu lassen.
compose_up() {
    cd "$SCRIPT_DIR" || return 1
    ensure_free_port
    UP_ARGS="-d"
    [ "${FORCE_RECREATE:-0}" = "1" ] && UP_ARGS="-d --force-recreate"
    # shellcheck disable=SC2086
    UP_OUT="$($DC up $UP_ARGS 2>&1)"
    UP_RC=$?
    printf '%s\n' "$UP_OUT"
    if [ $UP_RC -ne 0 ] && printf '%s' "$UP_OUT" \
        | grep -qiE "already allocated|address already in use|port is already"; then
        echo "  [PORTS] Der Port wurde uns im letzten Moment weggenommen -- suche neu ..."
        BLOCKED="$(get_port)"
        python3 "$SCRIPT_DIR/tools/port_check.py" --bump "$ENV_FILE" "$BLOCKED" 2>/dev/null || true
        ensure_free_port
        # shellcheck disable=SC2086
        $DC up $UP_ARGS
        UP_RC=$?
    fi
    return $UP_RC
}

# ── docker-compose.yml additiv aus .example ergaenzen ────────────────────────
merge_compose() {
    [ -f "$COMPOSE_EXAMPLE" ] || return 0
    if [ ! -f "$COMPOSE_FILE" ]; then
        cp "$COMPOSE_EXAMPLE" "$COMPOSE_FILE"
        echo "  [COMPOSE] docker-compose.yml aus .example erzeugt."
        return 0
    fi
    python3 - "$COMPOSE_FILE" "$COMPOSE_EXAMPLE" <<'PYEOF' || \
        echo "  [COMPOSE] additiver Merge uebersprungen (PyYAML fehlt) -- bestehende Compose bleibt."
import sys
try:
    import yaml
except ImportError:
    sys.exit(1)
cur_p, ex_p = sys.argv[1], sys.argv[2]
cur = yaml.safe_load(open(cur_p)) or {}
ex = yaml.safe_load(open(ex_p)) or {}
changed = False
for top in ('services', 'volumes', 'networks'):
    if top in ex:
        cur.setdefault(top, {})
        for k, v in (ex[top] or {}).items():
            if k not in cur[top]:
                cur[top][k] = v; changed = True
                print(f"  [COMPOSE] '{top}.{k}' ergaenzt.")
if changed:
    yaml.safe_dump(cur, open(cur_p, 'w'), sort_keys=False, default_flow_style=False)
else:
    print("  [COMPOSE] keine fehlenden Dienste -- unveraendert.")
PYEOF
}

# ── Update ───────────────────────────────────────────────────────────────────
do_update() {
    ZIP="$1"
    [ -n "$ZIP" ] || ZIP="$(find_latest_zip)"
    if [ -z "$ZIP" ] || [ ! -f "$ZIP" ]; then
        echo "Fehler: keine $AUTO_ZIP_BASENAME (oder ' (N)'-Variante) gefunden."
        echo "Lege die ZIP in: $SCRIPT_DIR"
        exit 1
    fi
    echo "=== Update aus: $ZIP ==="
    cd "$SCRIPT_DIR"
    detect_compose

    # Schritt 1: Self-Update von manage.sh AUS DER ZIP (dann Deploy neu starten).
    if [ -z "${DL_SELFUPDATED:-}" ]; then
        _extract_manage_from_zip "$ZIP" "$SCRIPT_DIR/manage.sh.new"
        if [ -f "$SCRIPT_DIR/manage.sh.new" ]; then
            if ! cmp -s "$SCRIPT_DIR/manage.sh.new" "$SCRIPT_DIR/manage.sh"; then
                chmod +x "$SCRIPT_DIR/manage.sh.new" 2>/dev/null || true
                mv "$SCRIPT_DIR/manage.sh.new" "$SCRIPT_DIR/manage.sh"
                echo "  [SELF-UPDATE] manage.sh aktualisiert -- starte neu ..."
                export DL_SELFUPDATED=1
                [ "$NO_CACHE" = "1" ] && exec "$SCRIPT_DIR/manage.sh" update --no-cache "$ZIP"
                exec "$SCRIPT_DIR/manage.sh" update "$ZIP"
            else
                rm -f "$SCRIPT_DIR/manage.sh.new"
                echo "  [SELF-UPDATE] manage.sh bereits aktuell."
            fi
        fi
    fi

    # Schritt 2: Code-Dateien aus ZIP entpacken (Configs/Nutzerdaten schuetzen).
    python3 - "$ZIP" <<'PYEOF'
import sys, os, zipfile
zip_path = sys.argv[1]
# Diese realen Configs NIE aus der ZIP ueberschreiben (kommen als .example):
PROTECT = {'.env', 'docker-compose.yml'}
# Diese Ordner sind Nutzerdaten -> komplett ignorieren, falls vorhanden:
PROTECT_DIRS = ('config/', 'downloads/')
with zipfile.ZipFile(zip_path) as z:
    members = z.namelist()
    written = 0; skipped = 0
    for m in members:
        if m.endswith('/'):
            continue
        norm = m.lstrip('/')
        base = os.path.basename(norm)
        if base == 'manage.sh':
            continue  # bereits per Self-Update behandelt
        if norm in PROTECT:
            skipped += 1; continue
        if any(norm.startswith(d) for d in PROTECT_DIRS) and os.path.isdir(norm.split('/')[0]):
            skipped += 1; continue
        dst = norm
        os.makedirs(os.path.dirname(dst) or '.', exist_ok=True)
        with z.open(m) as src, open(dst, 'wb') as out:
            out.write(src.read())
        written += 1
    print(f"  [CODE] {written} Datei(en) aktualisiert, {skipped} geschuetzt uebersprungen.")
PYEOF
    chmod +x "$SCRIPT_DIR/docker-entrypoint.sh" 2>/dev/null || true

    # Schritt 3: Configs aus .example anlegen, falls fehlend.
    [ -f "$ENV_FILE" ] || { cp "$ENV_EXAMPLE" "$ENV_FILE"; echo "  [ENV] .env aus .example erzeugt."; }
    mkdir -p "$SCRIPT_DIR/config" "$SCRIPT_DIR/downloads"

    # Schritt 4: freien Port vergeben (nur Erstinstallation) + Compose mergen.
    scan_and_assign_ports
    merge_compose

    # Schritt 5: Build + erzwungener Neuaufbau des Containers.
    # "up -d" allein laesst einen laufenden Container stehen, wenn compose
    # keine Aenderung an Image oder Konfiguration erkennt -- neue Features aus
    # der ZIP kaemen dann erst beim naechsten Zufall an. --force-recreate
    # ersetzt ihn in jedem Fall.
    echo "=== Baue & starte Container (Neuaufbau erzwungen) ==="
    BUILD_ARGS=""
    [ "$NO_CACHE" = "1" ] && BUILD_ARGS="--no-cache"
    # shellcheck disable=SC2086
    $DC build $BUILD_ARGS
    FORCE_RECREATE=1 compose_up

    # Schritt 6: aeltere Fassungen der Release-ZIP wegraeumen. Sonst gewinnt
    # beim naechsten Aufruf ohne Argument dieselbe "(N)"-Datei wieder, obwohl
    # laengst eine neuere ohne Klammer daneben liegt.
    RZ="$(_release_zip_py)" && python3 "$RZ" cleanup "$SCRIPT_DIR" "$AUTO_ZIP_BASENAME" "$ZIP" 2>/dev/null
    rm -f "$SCRIPT_DIR/.release_zip.py"
    echo ""
    print_url
    echo "=== Update abgeschlossen ==="
}

get_port() {
    P="$(grep -E '^DOWNLOADER_PORT=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d ' \r')"
    [ -n "$P" ] || P=8080
    echo "$P"
}

print_url() {
    P="$(get_port)"
    IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
    [ -n "$IP" ] || IP="localhost"
    echo "  WebGUI:  http://$IP:$P    (lokal: http://localhost:$P)"
}

# ── Kommandos ────────────────────────────────────────────────────────────────
do_watch() {
    echo "=== Watch-Modus: warte auf neue $AUTO_ZIP_BASENAME (Intervall ${WATCH_INTERVAL}s) ==="
    LAST=""
    while true; do
        Z="$(find_latest_zip)"
        if [ -n "$Z" ] && [ -f "$Z" ]; then
            SIG="$(python3 -c "import os,sys;print(os.path.getmtime(sys.argv[1]),os.path.getsize(sys.argv[1]))" "$Z" 2>/dev/null)"
            if [ "$SIG" != "$LAST" ]; then
                echo "[watch] neue ZIP erkannt: $Z"
                LAST="$SIG"
                "$SCRIPT_DIR/manage.sh" update "$Z" || echo "[watch] Update fehlgeschlagen."
            fi
        fi
        sleep "$WATCH_INTERVAL"
    done
}

CMD="${1:-}"
case "$CMD" in
    update)
        shift
        ZIP=""
        for a in "$@"; do
            case "$a" in
                --no-cache) NO_CACHE=1 ;;
                *) ZIP="$a" ;;
            esac
        done
        do_update "$ZIP"
        ;;
    watch)   do_watch ;;
    start)   detect_compose; cd "$SCRIPT_DIR"; compose_up; print_url ;;
    stop)    detect_compose; cd "$SCRIPT_DIR"; $DC down ;;
    restart) detect_compose; cd "$SCRIPT_DIR"; $DC down; compose_up; print_url ;;
    status)  detect_compose; cd "$SCRIPT_DIR"; $DC ps ;;
    logs)    detect_compose; cd "$SCRIPT_DIR"; shift; $DC logs -f "$@" ;;
    url)     print_url ;;
    port)
        detect_compose; cd "$SCRIPT_DIR"
        if [ -z "$2" ]; then
            echo "Aktueller WebGUI-Port: $(get_port)"
        else
            python3 - "$ENV_FILE" "$2" <<'PYEOF'
import sys, re
env, port = sys.argv[1], sys.argv[2]
lines = open(env).read().split('\n')
done = False
for i, l in enumerate(lines):
    if re.match(r'^\s*DOWNLOADER_PORT\s*=', l):
        lines[i] = f"DOWNLOADER_PORT={port}"; done = True
if not done: lines.append(f"DOWNLOADER_PORT={port}")
open(env, 'w').write('\n'.join(lines))
print(f"  Port auf {port} gesetzt.")
PYEOF
            compose_up
            print_url
        fi
        ;;
    *)
        echo "FAH Media Downloader – manage.sh"
        echo "Kommandos: update [<zip>] [--no-cache] | watch | start | stop |"
        echo "           restart | status | logs | url | port [<PORT>]"
        ;;
esac

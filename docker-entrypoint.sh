#!/bin/sh
# ============================================================================
# FAH Media Downloader – Container-Entrypoint – (c) 2026 Fabian Hammer
# ============================================================================
set -e

CONFIG_DIR="${CONFIG_DIR:-/config}"
DOWNLOADS_DIR="${DOWNLOADS_DIR:-/downloads}"
SHARES_DIR="${SHARES_DIR:-/mnt/shares}"
mkdir -p "$CONFIG_DIR" "$DOWNLOADS_DIR" "$SHARES_DIR" "$CONFIG_DIR/tmp"

# yt-dlp aktuell halten (Portale aendern sich staendig). Frueher lief das bei
# JEDEM Start ohne Zeitlimit -- auf einem NAS ohne Internet blockierte das den
# Start minutenlang. Jetzt: hoechstens einmal pro Tag und mit hartem Timeout.
STAMP="$CONFIG_DIR/.ytdlp-checked"
NOW="$(date +%s)"
LAST="$(cat "$STAMP" 2>/dev/null || echo 0)"
if [ "$((NOW - LAST))" -gt 86400 ]; then
    echo "[entrypoint] pruefe yt-dlp-Update ..."
    if timeout 90 pip install --no-cache-dir --upgrade \
            --timeout 15 --retries 1 "yt-dlp[curl-cffi]" >/dev/null 2>&1; then
        echo "$NOW" > "$STAMP"
        echo "[entrypoint] yt-dlp aktuell: $(yt-dlp --version 2>/dev/null || echo '?')"
    else
        echo "[entrypoint] yt-dlp-Update uebersprungen (offline?) -- nutze Image-Stand."
    fi
else
    echo "[entrypoint] yt-dlp heute bereits geprueft."
fi

# Der Port IM Container liegt in einem eigenen Netzwerk-Namensraum -- mit dem
# Host oder anderen Containern kann er nicht kollidieren. Was passieren kann:
# beim Neustart haengt der alte uvicorn-Prozess noch am Socket. Frueher starb
# der Container dann mit einer nichtssagenden Meldung; jetzt warten wir kurz
# und sagen im Zweifel klar, was los ist.
PORT="${PORT:-8080}"
i=0
while [ $i -lt 20 ]; do
    if python3 -c "
import socket, sys
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(('0.0.0.0', int('$PORT')))
except OSError:
    sys.exit(1)
finally:
    s.close()
" 2>/dev/null; then
        break
    fi
    [ $i -eq 0 ] && echo "[entrypoint] Port $PORT im Container noch belegt -- warte ..."
    i=$((i + 1))
    sleep 1
done
if [ $i -ge 20 ]; then
    echo "[entrypoint] Port $PORT ist im Container dauerhaft belegt."
    echo "[entrypoint] Das ist KEIN Konflikt mit dem Host -- dort sucht sich"
    echo "[entrypoint] manage.sh automatisch einen freien Port. Hier haengt ein"
    echo "[entrypoint] alter Prozess: 'docker restart' oder 'manage.sh restart'."
fi

exec uvicorn server:app --host 0.0.0.0 --port "$PORT" \
     --ws-max-size 16777216 --proxy-headers --forwarded-allow-ips '*'

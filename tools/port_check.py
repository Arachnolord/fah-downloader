#!/usr/bin/env python3
# ============================================================================
# Erdacht und programmiert von Fabian Hammer.
# (c) 2026 Fabian Hammer - Alle Rechte vorbehalten. Geistiges Eigentum.
# ============================================================================
"""Sorgt dafuer, dass der Dienst niemals an einem belegten Port scheitert.

Wird von manage.sh vor JEDEM Start aufgerufen. Die Erstinstallation sucht sich
zwar schon einen freien Port, fixiert ihn dann aber per Lock, damit die URL
stabil bleibt. Das genuegt nicht: Steht der Container still und ein anderer
Dienst greift sich in der Zwischenzeit den Port, bricht der naechste Start mit
"port is already allocated" ab.

Ausgewichen wird nur, wenn der Port wirklich FREMD belegt ist. Haelt ihn der
eigene (laufende) Container, bleibt alles, wie es ist -- ein blosser Bind-Test
wuerde hier faelschlich "belegt" melden und den Dienst vor sich selbst
fliehen lassen.

Aufruf:
    port_check.py <env-datei> "<NAME:STANDARD> ..." "<fremde ports>" "<eigene ports>"
    port_check.py --bump <env-datei> <blockierter port>
"""
import re
import socket
import sys


def _bindbar(port: int) -> bool:
    """Echter TCP-Bind statt einer gelesenen Liste -- nur das ist die Wahrheit.

    Eine Portliste aus `docker ps` kennt nur Container. Ein Dienst, der direkt
    auf dem NAS laeuft (oder ein Tunnel), taucht dort nicht auf.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _zahlen(roh: str) -> set:
    return {int(t) for t in str(roh or "").split() if t.isdigit()}


def _lies_ports(zeilen) -> dict:
    werte = {}
    for z in zeilen:
        m = re.match(r"^\s*([A-Za-z0-9_]+)\s*=\s*(\d{1,5})\s*$", z)
        if m:
            werte[m.group(1)] = int(m.group(2))
    return werte


def _setze(zeilen, name: str, port: int):
    for i, z in enumerate(zeilen):
        if re.match(rf"^\s*{re.escape(name)}\s*=", z):
            zeilen[i] = f"{name}={port}"
            return
    zeilen.append(f"{name}={port}")


def bump(env_datei: str, blockiert: int) -> int:
    """Nach einem verlorenen Rennen einen Schritt weiterruecken."""
    zeilen = open(env_datei, encoding="utf-8").read().split("\n")
    _setze(zeilen, "DOWNLOADER_PORT", blockiert + 1)
    open(env_datei, "w", encoding="utf-8").write("\n".join(zeilen))
    return blockiert + 1


def pruefe(env_datei: str, port_vars: str, fremd_roh: str, eigen_roh: str) -> int:
    fremd, eigen = _zahlen(fremd_roh), _zahlen(eigen_roh)
    paare = [p.split(":") for p in port_vars.split() if ":" in p]
    text = open(env_datei, encoding="utf-8").read()
    zeilen = text.split("\n")
    aktuell = _lies_ports(zeilen)

    def brauchbar(port: int, schon_vergeben: set) -> bool:
        if port in schon_vergeben or port in fremd:
            return False
        if port in eigen:       # unser eigener Container haelt ihn -- alles gut
            return True
        return _bindbar(port)

    vergeben, geaendert = set(), False
    for name, standard in paare:
        port = aktuell.get(name, int(standard))
        if brauchbar(port, vergeben):
            vergeben.add(port)
            continue
        alt, neu = port, port
        while not brauchbar(neu, vergeben):
            neu += 1
            if neu > 65500:
                print(f"  [PORTS] {name}: kein freier Port gefunden -- behalte {alt}.")
                neu = alt
                break
        vergeben.add(neu)
        if neu == alt:
            continue
        print(f"  [PORTS] {name}: {alt} ist fremd belegt -> weiche auf {neu} aus.")
        _setze(zeilen, name, neu)
        geaendert = True

    if geaendert:
        open(env_datei, "w", encoding="utf-8").write("\n".join(zeilen))
        print("  [PORTS] .env angepasst -- die WebGUI hat jetzt eine neue Adresse.")
    return 0


def main(argv) -> int:
    if len(argv) >= 4 and argv[1] == "--bump":
        neu = bump(argv[2], int(argv[3]))
        print(f"  [PORTS] Nachruecken auf {neu}.")
        return 0
    if len(argv) < 3:
        print(__doc__)
        return 2
    return pruefe(argv[1], argv[2],
                  argv[3] if len(argv) > 3 else "",
                  argv[4] if len(argv) > 4 else "")


if __name__ == "__main__":
    sys.exit(main(sys.argv))

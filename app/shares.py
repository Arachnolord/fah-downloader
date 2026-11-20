# ============================================================================
# Erdacht und programmiert von Fabian Hammer.
# (c) 2026 Fabian Hammer - Alle Rechte vorbehalten. Geistiges Eigentum.
# ============================================================================
"""NAS-/Server-Netzlaufwerke (SMB/CIFS) als Download-Ziel.

Der Nutzer traegt in der WebGUI ein Netzlaufwerk ein (Server, Freigabe, NAS-
Benutzer + Passwort). Beim Speichern wird die Freigabe per 'mount -t cifs' unter
SHARES_DIR/<name> eingehaengt und steht danach als Zielordner zur Verfuegung.
Die Zugangsdaten liegen -- wie alle Einstellungen -- persistent in CONFIG_DIR
(shares.json) und ueberleben Updates. Passwoerter werden mit dem Instanz-
Schluessel (secret.key) symmetrisch verschluesselt abgelegt, nie im Klartext.

Damit CIFS-Mounts im Container funktionieren, braucht der Container CAP_SYS_ADMIN
und /dev/fuse bzw. cifs-utils -- beides wird in docker-compose.yml.example
gesetzt.
"""
import os
import re
import subprocess
import tempfile
from pathlib import Path

from config import (SHARES_DIR, SHARES_FILE, decrypt_secret,
                    encrypt_secret, load_json, save_json)

# Verschluesselung liegt in config.py -- Freigaben und Seiten-Logins teilen sie.
_enc, _dec = encrypt_secret, decrypt_secret


def _safe_name(name: str) -> str:
    n = re.sub(r"[^A-Za-z0-9_.-]", "_", name).strip("_")
    return n or "share"


def list_shares() -> list:
    """Freigaben OHNE Passwort (fuer die GUI)."""
    data = load_json(SHARES_FILE, {"shares": []})
    out = []
    for s in data.get("shares", []):
        mp = SHARES_DIR / s["name"]
        out.append({
            "name": s["name"],
            "server": s["server"],
            "share": s["share"],
            "username": s.get("username", ""),
            "domain": s.get("domain", ""),
            "mountpoint": str(mp),
            "mounted": is_mounted(mp),
        })
    return out


def is_mounted(path: Path) -> bool:
    try:
        with open("/proc/mounts", "r") as f:
            return any(f" {path} " in line for line in f)
    except OSError:
        return os.path.ismount(path)


# So viel von der mount-Meldung bekommt der Nutzer zu sehen. Der Anfang
# benennt die Ursache ("mount error(13): Permission denied"); alles dahinter
# sind Hinweise auf dmesg und Manpages.
_FEHLER_MAX = 240


def _fehlermeldung(*ausgaben, geheim: str = "") -> str:
    """mount-stderr fuer den Client kuerzen und von Geheimnissen befreien.

    Die rohe Ausgabe ging frueher ungefiltert an den Browser. Sie kann die
    komplette Optionszeile wiederholen -- frueher samt password=... -- und
    ist mit Kernel-Hinweisen mehrere Zeilen lang.
    """
    text = ""
    for a in ausgaben:
        a = (a or "").strip()
        if a:
            text = a
            break
    if not text:
        return "mount fehlgeschlagen"
    if geheim:
        text = text.replace(geheim, "***")
    text = re.sub(r"(?i)(pass(?:word|wd)?\s*=\s*)[^,\s]*", r"\1***", text)
    zeilen = [z.strip() for z in text.splitlines() if z.strip()]
    text = " | ".join(zeilen[:3])
    if len(text) > _FEHLER_MAX:
        text = text[:_FEHLER_MAX].rstrip() + " …"
    return text


def _do_mount(server, share, username, password, domain, mountpoint: Path):
    mountpoint.mkdir(parents=True, exist_ok=True)
    if is_mounted(mountpoint):
        subprocess.run(["umount", "-l", str(mountpoint)], capture_output=True)
    for wert, was in ((username, "Benutzername"), (password, "Passwort"),
                      (domain, "Domaene")):
        if "\n" in str(wert or "") or "\r" in str(wert or ""):
            raise ValueError(f"{was} darf keinen Zeilenumbruch enthalten.")
    # //server/share -> mountpoint
    unc = f"//{server}/{share}"
    opts = "uid=0,gid=0,file_mode=0777,dir_mode=0777,vers=3.0"
    # Zugangsdaten gehen NICHT in die Optionszeile: die steht in der
    # Prozessliste (/proc/<pid>/cmdline) und in jeder Fehlermeldung von mount.
    # Stattdessen eine Datei mit 0600, die mount.cifs liest und die danach
    # sofort wieder verschwindet. Nebenbei sind damit auch Kommas im
    # Passwort kein Problem mehr.
    fd, cred_pfad = tempfile.mkstemp(prefix="cifs-", suffix=".cred")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            if username:
                fh.write(f"username={username}\n")
            fh.write(f"password={password or ''}\n")
            if domain:
                fh.write(f"domain={domain}\n")
        os.chmod(cred_pfad, 0o600)
        if username:
            opts = f"credentials={cred_pfad}," + opts
        else:
            opts = "guest," + opts
        r = subprocess.run(
            ["mount", "-t", "cifs", unc, str(mountpoint), "-o", opts],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            # Fallback: aeltere SMB-Version probieren
            opts2 = opts.replace("vers=3.0", "vers=2.0")
            r2 = subprocess.run(
                ["mount", "-t", "cifs", unc, str(mountpoint), "-o", opts2],
                capture_output=True, text=True,
            )
            if r2.returncode != 0:
                raise RuntimeError(_fehlermeldung(r.stderr, r2.stderr,
                                                  geheim=str(password or "")))
    finally:
        try:
            os.unlink(cred_pfad)
        except OSError:
            pass


def add_share(name, server, share, username, password, domain="") -> dict:
    name = _safe_name(name or share)
    mountpoint = SHARES_DIR / name
    _do_mount(server, share, username, password, domain, mountpoint)

    data = load_json(SHARES_FILE, {"shares": []})
    shares = [s for s in data.get("shares", []) if s["name"] != name]
    shares.append({
        "name": name,
        "server": server,
        "share": share,
        "username": username,
        "domain": domain,
        "password_enc": _enc(password),
    })
    data["shares"] = shares
    save_json(SHARES_FILE, data)
    return {"name": name, "mountpoint": str(mountpoint), "mounted": True}


def remove_share(name: str):
    name = _safe_name(name)
    mountpoint = SHARES_DIR / name
    if is_mounted(mountpoint):
        subprocess.run(["umount", "-l", str(mountpoint)], capture_output=True)
    data = load_json(SHARES_FILE, {"shares": []})
    data["shares"] = [s for s in data.get("shares", []) if s["name"] != name]
    save_json(SHARES_FILE, data)


def remount_all():
    """Beim Container-Start alle gespeicherten Freigaben wieder einhaengen."""
    data = load_json(SHARES_FILE, {"shares": []})
    for s in data.get("shares", []):
        mp = SHARES_DIR / s["name"]
        if is_mounted(mp):
            continue
        try:
            _do_mount(s["server"], s["share"], s.get("username", ""),
                      _dec(s.get("password_enc", "")), s.get("domain", ""), mp)
            print(f"[shares] '{s['name']}' wieder eingehaengt.")
        except Exception as e:
            print(f"[shares] '{s['name']}' konnte nicht eingehaengt werden: {e}")

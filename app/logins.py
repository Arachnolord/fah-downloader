# ============================================================================
# Erdacht und programmiert von Fabian Hammer.
# (c) 2026 Fabian Hammer - Alle Rechte vorbehalten. Geistiges Eigentum.
# ============================================================================
"""Zugangsdaten fuer einzelne Seiten.

Manche Seiten geben Videos nur angemeldeten Nutzern heraus -- Altersfreigaben,
Bezahlportale, private Kanaele. yt-dlp kann sich bei vielen davon selbst
anmelden, wenn es Benutzername und Passwort bekommt (Option `username` /
`password`, bei einzelnen Videos zusaetzlich `videopassword`).

Der frueher einzige Weg dafuer war eine aus dem Browser exportierte
Cookie-Datei. Die Datei bleibt als Rettungsanker bestehen -- Google laesst bei
Youtube keine Passwort-Anmeldung von Drittprogrammen zu -- aber fuer alles
andere reicht ab jetzt ein Eintrag in dieser Liste.

Gespeichert wird pro Domain. Beim Download wird der Eintrag gesucht, dessen
Domain zur URL passt (auch ueber Subdomains hinweg: ein Eintrag fuer
"beispiel.de" gilt auch fuer "www.beispiel.de"). Passwoerter liegen mit dem
Instanz-Schluessel verschluesselt in CONFIG_DIR/logins.json und verlassen den
Server nie wieder -- die WebGUI bekommt nur zu sehen, DASS eines gesetzt ist.
"""
import re
import threading
from urllib.parse import urlsplit

from config import (LOGINS_FILE, decrypt_secret, encrypt_secret, load_json,
                    save_json)

_lock = threading.RLock()
# Eine Domain, wie man sie eintippt: Buchstaben, Ziffern, Punkt, Bindestrich.
_DOMAIN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


def normalize_domain(roh: str) -> str:
    """Aus einer Eingabe die reine Domain machen.

    Der Nutzer darf alles hineinwerfen: "https://www.beispiel.de/video/x",
    "www.beispiel.de" oder "beispiel.de" -- heraus kommt immer "beispiel.de".
    Ein fuehrendes "www." faellt weg, sonst passte der Eintrag spaeter nicht
    auf Links ohne www.
    """
    text = str(roh or "").strip().lower()
    if not text:
        return ""
    if "://" in text:
        try:
            text = urlsplit(text).hostname or ""
        except ValueError:
            return ""
    else:
        text = text.split("/")[0].split("@")[-1]
    text = text.split(":")[0].strip(".")
    if text.startswith("www."):
        text = text[4:]
    return text if _DOMAIN.match(text) else ""


def _laden() -> dict:
    data = load_json(LOGINS_FILE, {"logins": []})
    return data if isinstance(data, dict) else {"logins": []}


def list_logins() -> list:
    """Alle Eintraege OHNE Passwort -- genau so geht es an die WebGUI."""
    out = []
    for e in _laden().get("logins", []):
        out.append({
            "domain": e.get("domain", ""),
            "username": e.get("username", ""),
            "has_password": bool(e.get("password_enc")),
            "has_videopassword": bool(e.get("videopassword_enc")),
            "note": e.get("note", ""),
        })
    return sorted(out, key=lambda e: e["domain"])


def save_login(domain: str, username: str, password: str = "",
               videopassword: str = "", note: str = "") -> dict:
    """Anlegen oder aendern. Ein leeres Passwort laesst das alte stehen.

    Sonst muesste der Nutzer sein Passwort jedes Mal neu tippen, nur weil er
    den Benutzernamen korrigiert -- die GUI kann es ihm ja nicht vorbefuellen.
    """
    dom = normalize_domain(domain)
    if not dom:
        raise ValueError("Keine gueltige Domain.")
    with _lock:
        data = _laden()
        eintraege = [e for e in data.get("logins", []) if isinstance(e, dict)]
        vorher = next((e for e in eintraege if e.get("domain") == dom), None)
        neu = {
            "domain": dom,
            "username": str(username or "").strip(),
            "password_enc": (encrypt_secret(password) if password
                             else (vorher or {}).get("password_enc", "")),
            "videopassword_enc": (encrypt_secret(videopassword) if videopassword
                                  else (vorher or {}).get("videopassword_enc", "")),
            "note": str(note or "").strip()[:200],
        }
        eintraege = [e for e in eintraege if e.get("domain") != dom] + [neu]
        save_json(LOGINS_FILE, {"logins": eintraege})
    return {"domain": dom, "username": neu["username"],
            "has_password": bool(neu["password_enc"]),
            "has_videopassword": bool(neu["videopassword_enc"]),
            "note": neu["note"]}


def delete_login(domain: str) -> bool:
    dom = normalize_domain(domain)
    with _lock:
        data = _laden()
        eintraege = [e for e in data.get("logins", []) if isinstance(e, dict)]
        rest = [e for e in eintraege if e.get("domain") != dom]
        if len(rest) == len(eintraege):
            return False
        save_json(LOGINS_FILE, {"logins": rest})
    return True


def login_for(url: str) -> dict:
    """Passenden Eintrag zu einer URL finden -- {} wenn keiner passt.

    Gesucht wird von der genauesten Uebereinstimmung her: ein Eintrag fuer
    "kanal.beispiel.de" gewinnt gegen einen fuer "beispiel.de".
    """
    roh = str(url or "").strip()
    if not roh:
        return {}
    if "://" not in roh:
        roh = "https://" + roh
    try:
        host = (urlsplit(roh).hostname or "").strip(".").lower()
    except ValueError:
        return {}
    if not host:
        return {}
    nach_domain = {e.get("domain"): e for e in _laden().get("logins", [])
                   if isinstance(e, dict)}
    teile = host.split(".")
    for i in range(len(teile) - 1):
        treffer = nach_domain.get(".".join(teile[i:]))
        if treffer:
            return {
                "username": treffer.get("username", ""),
                "password": decrypt_secret(treffer.get("password_enc", "")),
                "videopassword": decrypt_secret(
                    treffer.get("videopassword_enc", "")),
            }
    return {}

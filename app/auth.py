# ============================================================================
# Erdacht und programmiert von Fabian Hammer.
# (c) 2026 Fabian Hammer - Alle Rechte vorbehalten. Geistiges Eigentum.
# ============================================================================
"""Login fuer die WebGUI.

Der Admin-Zugang wird persistent in CONFIG_DIR/users.json gehalten. Erst-Login:
Ist noch kein Nutzer angelegt, kann per ENV (ADMIN_USER/ADMIN_PASSWORD) ein
Start-Account gesetzt werden -- sonst wird beim ersten Aufruf ein Setup-Dialog
gezeigt. Passwoerter werden nur als PBKDF2-Hash gespeichert (nie im Klartext).
Sessions laufen ueber ein signiertes Cookie (itsdangerous), Schluessel liegt in
CONFIG_DIR/secret.key und ueberlebt Updates.
"""
import hashlib
import hmac
import os
import secrets
import threading
import time

from itsdangerous import BadSignature, URLSafeTimedSerializer

from config import USERS_FILE, get_secret, load_json, save_json

SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 Tage
_serializer = URLSafeTimedSerializer(get_secret(), salt="downloader-session")
_setup_lock = threading.Lock()
# Fester Salt fuer die Attrappen-Rechnung bei unbekanntem Benutzer (siehe
# verify): der Wert ist egal, er muss nur ein gueltiger Hex-String sein.
_DUMMY_SALT = "00" * 16


class SchonEingerichtet(RuntimeError):
    """Es gibt bereits einen Admin -- die Ersteinrichtung ist vorbei."""


def _hash_pw(password: str, salt: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000)
    return dk.hex()


def _users() -> dict:
    return load_json(USERS_FILE, {"users": {}})


def has_admin() -> bool:
    return bool(_users().get("users"))


def create_user(username: str, password: str):
    if not username or not password:
        raise ValueError("Benutzername und Passwort erforderlich.")
    data = _users()
    salt = secrets.token_hex(16)
    data.setdefault("users", {})[username] = {
        "salt": salt,
        "hash": _hash_pw(password, salt),
        "created": int(time.time()),
    }
    save_json(USERS_FILE, data)


def setup_admin(username: str, password: str):
    """Ersteinrichtung: Pruefung UND Anlegen unter einem Lock.

    Ohne das konnten zwei gleichzeitige Erstaufrufe beide has_admin() als
    False sehen und zwei Konten anlegen -- das zweite haette sich still an
    einer laufenden Einrichtung vorbeigeschmuggelt.
    """
    with _setup_lock:
        if has_admin():
            raise SchonEingerichtet()
        create_user(username, password)


def verify(username: str, password: str) -> bool:
    u = _users().get("users", {}).get(username)
    if not u:
        # Trotzdem einmal PBKDF2 rechnen: sonst antwortet ein unbekannter
        # Benutzer in Mikrosekunden, ein bekannter erst nach der Hash-Rechnung
        # -- an der Antwortzeit liesse sich ablesen, welche Namen existieren.
        _hash_pw(password, _DUMMY_SALT)
        return False
    return hmac.compare_digest(u["hash"], _hash_pw(password, u["salt"]))


def bootstrap_from_env():
    """Legt beim ersten Start optional einen Admin aus ENV an."""
    if has_admin():
        return
    user = os.environ.get("ADMIN_USER", "").strip()
    pw = os.environ.get("ADMIN_PASSWORD", "").strip()
    if user and pw:
        create_user(user, pw)
        print(f"[auth] Admin '{user}' aus ENV angelegt.")


def make_session(username: str) -> str:
    return _serializer.dumps({"u": username})


def read_session(token: str):
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
        return data.get("u")
    except BadSignature:
        return None
    except Exception:
        return None

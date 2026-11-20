"""End-to-End-Test der WebGUI-API gegen einen echt laufenden uvicorn."""
import os
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent   # Deploy-Wurzel
ROOT = tempfile.mkdtemp(prefix="dltest-")
CFG, DL, HOST, SH = (Path(ROOT) / n for n in ("config", "downloads", "hostfs", "shares"))
for d in (CFG, DL, HOST, SH):
    d.mkdir(parents=True)

# ── Altbestand simulieren: Verlaufs-DB im ALTEN Schema mit einem Eintrag ────
old = sqlite3.connect(CFG / "history.sqlite")
old.execute("""CREATE TABLE history(
    id TEXT PRIMARY KEY, url TEXT, title TEXT, filename TEXT, dest TEXT,
    status TEXT, error TEXT, filesize INTEGER, finished INTEGER)""")
old.execute("INSERT INTO history VALUES('altjob','http://alt/x','Alter Eintrag',"
            "'alt.mp4','/downloads','done','',123,1700000000)")
old.commit()
old.close()

os.environ.update(CONFIG_DIR=str(CFG), DOWNLOADS_DIR=str(DL),
                  HOSTFS_DIR=str(HOST), SHARES_DIR=str(SH))
sys.path.insert(0, str(BASE / "app"))

import uvicorn                                              # noqa: E402
import httpx                                                # noqa: E402
from server import app                                      # noqa: E402
from version import VERSION                                 # noqa: E402

PORT = 8791
cfg = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error")
srv = uvicorn.Server(cfg)
threading.Thread(target=srv.run, daemon=True).start()
for _ in range(100):
    time.sleep(0.1)
    try:
        httpx.get(f"http://127.0.0.1:{PORT}/health", timeout=1)
        break
    except Exception:
        pass

U = f"http://127.0.0.1:{PORT}"
ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  \033[32mOK\033[0m   {name}")
    else:
        fail += 1
        print(f"  \033[31mFAIL\033[0m {name} {extra}")


c = httpx.Client(base_url=U, follow_redirects=False, timeout=20)

print("\n── Auth ──")
check("Ungeschuetzt: /health", c.get("/health").status_code == 200)
check("API ohne Login -> 401", c.get("/api/settings").status_code == 401)
check("Seite ohne Login -> Redirect", c.get("/").status_code == 307)
check("needs_setup = true", c.get("/api/needs-setup").json()["needs_setup"])
r = c.post("/api/setup", json={"username": "fab", "password": "geheim123"})
check("Ersteinrichtung", r.status_code == 200 and "session" in c.cookies)
check("API mit Login -> 200", c.get("/api/settings").status_code == 200)
r = httpx.post(f"{U}/api/setup", json={"username": "zweiter", "password": "geheim123"})
check("Zweite Ersteinrichtung -> 400 already-set",
      r.status_code == 400 and r.json().get("error") == "already-set",
      f"-> {r.status_code} {r.text[:80]}")
check("Falsches Passwort -> 401",
      httpx.post(f"{U}/api/login", json={"username": "fab", "password": "x"})
      .status_code == 401)
check("Unbekannter Nutzer -> 401 (gleiche Antwort wie falsches Passwort)",
      httpx.post(f"{U}/api/login", json={"username": "niemand", "password": "x"})
      .status_code == 401)

print("\n── Icons ──")
for p, typ in (("/favicon.ico", "image/x-icon"), ("/favicon.svg", "image/svg+xml"),
               ("/apple-touch-icon.png", "image/png"),
               ("/site.webmanifest", "application/manifest+json")):
    r = c.get(p)
    check(f"{p}", r.status_code == 200 and typ in r.headers["content-type"],
          f"-> {r.status_code}")
ico = c.get("/favicon.ico").content
check("favicon.ico ist ein gueltiger ICO-Container",
      ico[:4] == b"\x00\x00\x01\x00" and int.from_bytes(ico[4:6], "little") == 3)
check("index.html Cache-Buster ersetzt",
      "__VERSION__" not in c.get("/").text
      and f"app.js?v={VERSION}" in c.get("/").text)

print("\n── Verlaufs-Migration ──")
h = c.get("/api/history").json()
check("Alter Eintrag ueberlebt die Migration",
      any(x["id"] == "altjob" for x in h["history"]))
rec = [x for x in h["history"] if x["id"] == "altjob"][0]
check("Neue Felder mit Standardwerten befuellt",
      rec["files"] == [] and rec["opts"] == {} and rec["exists"] is False)
check("Zaehler stimmen", h["counts"]["done"] == 1 and h["counts"]["all"] == 1)

print("\n── Einstellungen: Typen & Migration ──")
c.put("/api/settings", json={"concurrent_downloads": "5", "sponsorblock": "true",
                             "custom_format": "mkv", "format_mode": "custom"})
s = c.get("/api/settings").json()
check("Zahl aus String wird int", s["concurrent_downloads"] == 5,
      f'-> {s["concurrent_downloads"]!r}')
check("Bool aus String wird bool", s["sponsorblock"] is True)
check('custom_format "mkv" -> video_container', s["video_container"] == "mkv",
      f'-> {s["video_container"]!r}')
check("kaputtes custom_format geleert", s["custom_format"] == "")
check("format_mode von custom zurueckgesetzt", s["format_mode"] == "best")
check("Alter Modus mp4 wird migriert",
      c.put("/api/settings", json={"format_mode": "mp4", "video_container": "auto"})
      .json()["video_container"] == "mp4")

print("\n── Pfad-Absicherung ──")
check("Download-Ziel ausserhalb -> 403",
      c.post("/api/download", json={"url": "http://x/y", "dest": "/etc"}).status_code == 403)
check("Traversal im Ziel -> 403",
      c.post("/api/download",
             json={"url": "http://x/y", "dest": str(DL) + "/../../etc"}).status_code == 403)
check("Standard-Ziel ausserhalb -> 403",
      c.put("/api/settings", json={"default_dest": "/etc"}).status_code == 403)
check("browse ausserhalb -> 403", c.get("/api/browse?path=/etc").status_code == 403)
check("mkdir ausserhalb -> 400/403",
      c.post("/api/mkdir", json={"path": "/etc", "name": "x"}).status_code in (400, 403))
check("unbekanntes Zielformat -> 400",
      c.post("/api/download",
             json={"url": "http://x/y", "video_container": "rar"}).status_code == 400)
check("unbekannte Playlist-Auswahl -> 400",
      c.post("/api/download",
             json={"url": "http://x/y", "playlist_mode": "beides"}).status_code == 400)
check("Playlist-Auswahl 'single' wird angenommen",
      c.post("/api/download",
             json={"url": "http://example.invalid/nix.mp4",
                   "playlist_mode": "single"}).status_code == 200)

print("\n── Adress-Pruefung (Schema, eigenes Netz) ──")
for url in ("http://127.0.0.1:1/nix.mp4", "http://192.168.1.23/x", "http://192.168.1.5/x",
            "http://172.16.0.1/x", "http://169.254.169.254/latest", "http://[::1]/x",
            "http://[fd00::1]/x", "http://localhost:8080/x", "http://0.0.0.0/x",
            "http://2130706433/x", "http://[::ffff:127.0.0.1]/x"):
    r = c.post("/api/download", json={"url": url})
    check(f"{url:36} -> 400", r.status_code == 400
          and "nicht erlaubt" in r.json().get("error", ""),
          f"-> {r.status_code} {r.text[:100]}")
for url in ("ftp://example.invalid/x.mp4", "file:///etc/passwd",
            "rtsp://example.invalid/stream"):
    r = c.post("/api/download", json={"url": url})
    check(f"{url:36} -> 400 (nur http/https)", r.status_code == 400
          and "http" in r.json().get("error", ""), f"-> {r.status_code} {r.text[:100]}")
r = c.post("/api/download", json={"urls": ["http://example.invalid/ok\nhttp://127.0.0.1/nein"]})
check("Gemischte Liste: eine eingereiht, eine abgelehnt mit Grund",
      r.status_code == 200 and len(r.json()["added"]) == 1
      and len(r.json()["abgelehnt"]) == 1
      and r.json()["abgelehnt"][0]["url"] == "http://127.0.0.1/nein"
      and "nicht erlaubt" in r.json()["abgelehnt"][0]["grund"],
      f"-> {r.status_code} {r.text[:160]}")
r = c.post("/api/download", json={"url": "example.invalid/ohne-schema"})
check("Adresse ohne Schema wird angenommen (yt-dlp ergaenzt https)",
      r.status_code == 200 and len(r.json()["added"]) == 1, f"-> {r.status_code}")
conn = sqlite3.connect(CFG / "history.sqlite")
conn.execute("INSERT INTO history(id,url,title,status,opts,files,finished) "
             "VALUES('privat1','http://192.168.0.9/a','P','error','{}','[]',1)")
conn.commit()
conn.close()
r = c.post("/api/history/privat1/retry")
check("Wiederaufnahme prueft die Adresse ebenfalls (-> 400)",
      r.status_code == 400 and "nicht erlaubt" in r.json().get("error", ""),
      f"-> {r.status_code} {r.text[:100]}")
check("Playlist-Vorgabe laesst sich speichern",
      c.put("/api/settings", json={"playlist_mode": "single"})
      .json()["playlist_mode"] == "single")
check("Unsinnige Vorgabe wird auf 'playlist' zurechtgerueckt",
      c.put("/api/settings", json={"playlist_mode": "quatsch"})
      .json()["playlist_mode"] == "playlist")

print("\n── Ordner-Browser ──")
(DL / "Filme").mkdir()
rts = c.get("/api/roots").json()
check("Wurzeln nennen den erlaubten Bereich (Hinweistext)",
      isinstance(rts.get("hinweis"), str) and "HOSTFS_MOUNT" in rts["hinweis"],
      f'-> {rts.get("hinweis")!r}')
check("Host-Wurzel ist ohne DOWNLOAD_ROOTS der ganze Mount",
      any(r_["path"] == str(HOST) for r_ in rts["roots"]), f'-> {rts["roots"]}')
check("Neue Einstellung job_timeout_h wird ausgeliefert (Standard 6)",
      c.get("/api/settings").json().get("job_timeout_h") == 6)
check("job_timeout_h laesst sich als Zahl speichern",
      c.put("/api/settings", json={"job_timeout_h": "12"}).json()["job_timeout_h"] == 12)
b = c.get(f"/api/browse?path={DL}").json()
check("Wurzel hat kein Elternteil (frueher 403-Sackgasse)", b["parent"] is None,
      f'-> {b["parent"]!r}')
check("Unterordner gelistet", [d["name"] for d in b["dirs"]] == ["Filme"])
sub = c.get(f"/api/browse?path={DL / 'Filme'}").json()
# macOS legt Temp unter /var (Symlink auf /private/var) an -> aufgeloest vergleichen.
check("Unterordner hat Elternteil", sub["parent"] == str(DL.resolve()))

print("\n── Duplikat-Erkennung ──")
r = c.post("/api/download", json={"urls": ["http://example.invalid/a\nhttp://example.invalid/a"]}).json()
check("Doppelte URL wird uebersprungen",
      len(r["added"]) == 1 and len(r["skipped"]) == 1,
      f'added={len(r["added"])} skipped={len(r["skipped"])}')

print("\n── Datei ausliefern (Range/Streaming) ──")
media = DL / "clip.mp4"
payload = bytes(range(256)) * 400          # 102.400 Bytes
media.write_bytes(payload)
conn = sqlite3.connect(CFG / "history.sqlite")
conn.execute("INSERT INTO history(id,url,title,filename,dest,status,error,filesize,"
             "finished,files,opts,log,duration,ext) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
             ("vid1", "http://x/v", "Mein Clip", "clip.mp4", str(DL), "done", "",
              len(payload), int(time.time()), f'["{media}"]', "{}", "", 12, "mp4"))
# Eintrag, dessen Datei ausserhalb der erlaubten Wurzeln liegt
conn.execute("INSERT INTO history(id,url,title,status,files,finished) "
             "VALUES('evil','http://x/e','Boese','done','[\"/etc/hosts\"]',1)")
conn.commit()
conn.close()

r = c.get("/api/file/vid1")
check("Volle Datei -> 200", r.status_code == 200 and r.content == payload)
check("Accept-Ranges gesetzt", r.headers.get("accept-ranges") == "bytes")
check("Korrekter MIME-Typ", r.headers["content-type"].startswith("video/mp4"))
check("inline (Player)", r.headers["content-disposition"].startswith("inline"))

r = c.get("/api/file/vid1", headers={"Range": "bytes=100-199"})
check("Range -> 206", r.status_code == 206, f"-> {r.status_code}")
check("Range liefert genau die Bytes", r.content == payload[100:200])
check("Content-Range korrekt",
      r.headers["content-range"] == f"bytes 100-199/{len(payload)}")
check("Content-Length korrekt", r.headers["content-length"] == "100")

r = c.get("/api/file/vid1", headers={"Range": "bytes=-50"})
check("Suffix-Range (letzte 50 Bytes)", r.status_code == 206 and r.content == payload[-50:])
r = c.get("/api/file/vid1", headers={"Range": "bytes=99999999-"})
check("Range hinter Dateiende -> 416", r.status_code == 416, f"-> {r.status_code}")
r = c.get("/api/file/vid1", headers={"Range": "bytes=500-"})
check("Offene Range bis Dateiende", r.status_code == 206 and r.content == payload[500:])

check("Download-Variante setzt attachment",
      c.get("/api/file/vid1?dl=1").headers["content-disposition"].startswith("attachment"))
check("Datei ausserhalb der Wurzeln -> 404", c.get("/api/file/evil").status_code == 404)
check("Unbekannte Job-ID -> 404", c.get("/api/file/gibtsnicht").status_code == 404)
check("Ungueltiger Index -> 404", c.get("/api/file/vid1?idx=9").status_code == 404)
check("Datei-Endpunkt braucht Login",
      httpx.get(f"{U}/api/file/vid1").status_code == 401)

print("\n── Bibliothek: Rubriken, Suche, Loeschen ──")
check("Rubrik done", all(x["status"] == "done"
                         for x in c.get("/api/history?status=done").json()["history"]))
check("Suche findet", len(c.get("/api/history?q=Mein Clip").json()["history"]) == 1)
check("Suche ohne Treffer", c.get("/api/history?q=zzzz").json()["history"] == [])
check("playable-Flag fuer mp4",
      [x for x in c.get("/api/history").json()["history"] if x["id"] == "vid1"][0]["playable"])
check("exists-Flag korrekt",
      [x for x in c.get("/api/history").json()["history"] if x["id"] == "vid1"][0]["exists"])

check("Eintrag loeschen ohne Datei",
      c.request("DELETE", "/api/history/altjob").status_code == 200)
check("Geloeschter Eintrag ist weg",
      not any(x["id"] == "altjob" for x in c.get("/api/history").json()["history"]))
check("Loeschen mit Datei entfernt die Datei",
      c.request("DELETE", "/api/history/vid1?file=1").json()["removed_files"] == [str(media.resolve())]
      and not media.exists())
check("Datei ausserhalb wird NICHT geloescht",
      c.request("DELETE", "/api/history/evil?file=1").json()["removed_files"] == []
      and Path("/etc/hosts").exists())
check("Loeschen unbekannt -> 404",
      c.request("DELETE", "/api/history/nixda").status_code == 404)

print("\n── Wiederaufnahme ──")
conn = sqlite3.connect(CFG / "history.sqlite")
conn.execute("INSERT INTO history(id,url,title,status,opts,files,finished,error) "
             "VALUES('fail1','http://example.invalid/z','Kaputt','error',"
             "'{\"format_mode\":\"audio\",\"audio_format\":\"opus\"}','[]',1,'Boom')")
conn.commit()
conn.close()
r = c.post("/api/history/fail1/retry")
check("Retry reiht neu ein", r.status_code == 200 and r.json()["job"]["url"]
      == "http://example.invalid/z")
check("Retry unbekannt -> 404", c.post("/api/history/nixda/retry").status_code == 404)

print("\n── Rubrik leeren ──")
# Deterministisch: eigene Zeilen anlegen statt gegen die im Hintergrund noch
# fehlschlagenden Test-Downloads zu messen.
conn = sqlite3.connect(CFG / "history.sqlite")
for jid, st in (("clr-d", "done"), ("clr-e", "error"), ("clr-c", "canceled")):
    conn.execute("INSERT INTO history(id,url,title,status,files,opts,log,finished)"
                 " VALUES(?,?,?,?,'[]','{}','',5)", (jid, "u", "T", st))
conn.commit()
conn.close()
c.post("/api/clear-history?status=error")
ids = {h["id"] for h in c.get("/api/history").json()["history"]}
check("Rubrik error geleert", "clr-e" not in ids)
check("Rubrik done bleibt unberuehrt", "clr-d" in ids)
check("Rubrik canceled bleibt unberuehrt", "clr-c" in ids)

print("\n── Cookies ──")
check("Ohne Datei: present=false", c.get("/api/cookies").json()["present"] is False)
check("Muell wird abgelehnt",
      c.post("/api/cookies", files={"file": ("c.txt", b"kein cookie")}).status_code == 400)
netscape = b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tX\t1\n"
check("Netscape-Datei wird angenommen",
      c.post("/api/cookies", files={"file": ("c.txt", netscape)}).status_code == 200)
check("Danach present=true", c.get("/api/cookies").json()["present"] is True)
check("Cookie-Datei liegt in config/", (CFG / "cookies.txt").exists())
check("Rechte 0600", oct((CFG / "cookies.txt").stat().st_mode)[-3:] == "600")
check("Loeschen", c.request("DELETE", "/api/cookies").status_code == 200
      and not (CFG / "cookies.txt").exists())

print("\n── Warteschlange / Abbruch ──")
q = c.get("/api/queue").json()["jobs"]
check("Warteschlange liefert aktive Jobs", isinstance(q, list))
if q:
    jid = q[0]["id"]
    check("Abbrechen bestaetigt", c.post(f"/api/cancel/{jid}").json()["ok"] is True)
check("Abbrechen unbekannt -> ok:false",
      c.post("/api/cancel/gibtsnicht").json()["ok"] is False)
check("cancel-all antwortet", c.post("/api/cancel-all").json()["ok"] is True)

print("\n── WebSocket ──")
try:
    from websockets.sync.client import connect as wsconnect
    ck = "; ".join(f"{k}={v}" for k, v in c.cookies.items())
    with wsconnect(f"ws://127.0.0.1:{PORT}/ws",
                   additional_headers={"Cookie": ck}) as w:
        import json as _j
        snap = _j.loads(w.recv(timeout=5))
        check("Snapshot beim Verbinden",
              snap["type"] == "snapshot" and snap["version"] == VERSION)
    try:
        with wsconnect(f"ws://127.0.0.1:{PORT}/ws") as w:
            w.recv(timeout=3)
        check("WS ohne Cookie abgewiesen", False, "-> Verbindung stand offen")
    except Exception:
        check("WS ohne Cookie abgewiesen", True)
except ImportError:
    print("  (websockets nicht installiert – uebersprungen)")

print(f"\n{'='*54}\n  {ok} bestanden, {fail} fehlgeschlagen\n{'='*54}")
srv.should_exit = True
sys.exit(1 if fail else 0)

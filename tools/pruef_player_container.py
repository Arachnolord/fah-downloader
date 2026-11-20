# ============================================================================
# Erdacht und programmiert von Fabian Hammer.
# (c) 2026 Fabian Hammer - Alle Rechte vorbehalten. Geistiges Eigentum.
# ============================================================================
"""Prueft den Player-Remux gegen ECHTES ffmpeg -- laeuft IM Container.

Auf einem Entwicklungsrechner ohne ffmpeg laesst sich nur pruefen, WAS mit
einer Datei geschehen soll. Ob der ffmpeg-Aufruf danach wirklich durchlaeuft,
zeigt erst dieser Test. Genau so wurde gefunden, dass ffmpeg das Ausgabeformat
aus der Endung ableitet und an der Zwischendatei "....teil" scheitert.

    scp tools/pruef_player_container.py <nas>:/volume3/docker/fah-downloader/config/
    ssh <nas> docker exec fah-downloader python /config/pruef_player_container.py
"""
import os, pathlib, subprocess, sys, tempfile, time
sys.path.insert(0, "/app")
import playback

R = tempfile.mkdtemp()
quelle = os.path.join(R, "probe.mkv")
# h264 + AC3 in MKV: genau der Fall, der im Browser scheitert -- MKV kann er
# nicht, und AC3 darf auch in einer MP4-Huelle nicht bleiben.
subprocess.run(["ffmpeg", "-v", "error", "-y",
                "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=4",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "ac3",
                quelle], check=True)
print("Testdatei:", os.path.getsize(quelle), "Bytes")

vorher = playback.pruefe_datei(quelle)
print("Spuren vorher:", vorher)
plan = playback.plan_fuer(quelle)
print("Plan:", {k: v for k, v in plan.items() if k != "spuren"})
assert plan["modus"] == "umpacken" and plan["ziel"] == ".mp4", plan

playback.CACHE_DIR = pathlib.Path(R) / "cache"
playback.vorbereiten(quelle, 4 * 1024 ** 3)
st = {}
for _ in range(180):
    st = playback.status(quelle, ".mp4")
    if st["bereit"] or st["fehler"]:
        break
    time.sleep(0.5)
print("Status:", st)
assert st.get("bereit"), st
ziel = playback.cache_pfad(quelle, ".mp4")
print("Ergebnis:", ziel.name, os.path.getsize(ziel), "Bytes")

nach = playback.pruefe_datei(str(ziel))
print("Spuren nachher:", nach)
assert nach["video"] == "h264", nach          # Bild unveraendert kopiert
assert nach["audio"] == "aac", nach           # nur der Ton gewandelt
assert abs(nach["dauer"] - 4) < 1, nach
kopf = open(ziel, "rb").read(64)
print("moov liegt vorne (Spulen moeglich):", b"moov" in kopf)
assert b"moov" in kopf

# Zweiter Aufruf darf nicht erneut rechnen
t0 = time.time()
r2 = playback.vorbereiten(quelle, 4 * 1024 ** 3)
print("Zweiter Aufruf:", r2.get("bereit"), f"{time.time() - t0:.3f}s")
assert r2.get("bereit") is True

# Bildspur, die kein Browser darstellen kann -> ehrlicher Hinweis
hevc = os.path.join(R, "probe_hevc.mkv")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", quelle,
                "-c:v", "libx265", "-preset", "ultrafast", "-c:a", "copy",
                hevc], check=True)
plan2 = playback.plan_fuer(hevc)
print("HEVC-Plan:", plan2["modus"], "|", plan2["grund"][:80])
assert plan2["modus"] == "nein", plan2

# VP9 + Opus gehoert nach WebM, nicht nach MP4
webm_quelle = os.path.join(R, "probe_vp9.mkv")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", quelle,
                "-c:v", "libvpx-vp9", "-b:v", "200k", "-cpu-used", "8",
                "-c:a", "libopus", webm_quelle], check=True)
plan3 = playback.plan_fuer(webm_quelle)
print("VP9-Plan:", plan3["modus"], plan3["ziel"])
assert plan3["modus"] == "umpacken" and plan3["ziel"] == ".webm", plan3

print("ALLE PRUEFUNGEN BESTANDEN")

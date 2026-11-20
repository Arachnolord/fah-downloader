# Tests

Prüfen die Anwendung ohne Docker gegen einen echt laufenden Server bzw. einen
lokalen HTTP-Server, der Downloads simuliert. Kein Internetzugang nötig.

```sh
python3 -m venv /tmp/dltest
/tmp/dltest/bin/pip install -r app/requirements.txt httpx
/tmp/dltest/bin/python tests/test_api.py      # WebGUI-API, 102 Prüfungen
/tmp/dltest/bin/python tests/test_engine.py   # Download-Engine, 100 Prüfungen
/tmp/dltest/bin/python tests/test_subdirs.py  # Ablage in Unterordnern, 95 Prüfungen
/tmp/dltest/bin/python tests/test_bibliothek.py # Rubriken, Logins, Einsortieren, 61 Prüfungen
/tmp/dltest/bin/python tests/test_sicherheit.py # Härtungen 2.5.1, 100 Prüfungen
/tmp/dltest/bin/python tests/test_release_zip.py # ZIP-Auswahl + manage.sh-Ganzlauf, 37 Prüfungen
/tmp/dltest/bin/python tests/test_ports.py     # Automatische Portwahl, 27 Prüfungen
/tmp/dltest/bin/python tests/test_cancel.py   # Abbruch-Mechanik von yt-dlp
```

Alles läuft in temporären Verzeichnissen – `config/` und `downloads/` der
Installation werden nicht angefasst.

| Datei | Deckt ab |
|---|---|
| `test_api.py` | Login (zweites Setup, unbekannter Nutzer), Icons, Verlaufs-Migration, Einstellungs-Typen, Pfad-Absicherung, Adress-Prüfung (Schema, eigenes Netz, gemischte Listen, Wiederaufnahme), Ordner-Browser mit Hinweistext, Range-Streaming, Rubriken, Löschen, Wiederaufnahme, Cookies, WebSocket |
| `test_engine.py` | Download bis zum Ende, Ablage im Unterordner, Playlist-/Einzelvideo-Auswahl, Drosselung der Fortschrittsmeldungen, Abbruch mitten im Lauf, Erhalt der `.part`-Datei, echte Wiederaufnahme per Range-Anfrage, Zielformat-Postprocessor, Job-Protokoll, Zeitlimit je Auftrag (Wächter, `socket_timeout`, Fortsetzen danach), Duplikat-Erkennung in `enqueue()`, Browser-Tarnung, Titelbild nur bei passendem Zielformat (kein WAV/WEBM), Rettung aus dem Zwischenordner nach abgebrochener Nachbearbeitung samt Altlasten-Reparatur beim Start |
| `test_sicherheit.py` | `DOWNLOAD_ROOTS` (Bereinigung, nur Teilbäume wählbar, Hinweistext), Adress-Prüfung als Tabelle (private Bereiche, Dezimal-/Oktal-IPs, IPv4-mapped IPv6, Schemata, Suchpräfixe), Anmeldung (Dummy-PBKDF2, 12 gleichzeitige Ersteinrichtungen), NAS-Freigabe mit nachgebautem `mount` (credentials-Datei 0600, Passwort nie in argv/Fehlermeldung, Fallback vers=2.0), Umpacker mit nachgebautem `ffmpeg` (400 KB stderr, Fehlschlag, Hänger wird abgeschossen) |
| `test_subdirs.py` | Quellname aus der URL (Subdomains, mehrteilige TLDs, IP-Hosts, Punycode, Suchanfragen), Unterpfad aus Quelle/Art/Jahr/Monat, Festschreiben für die Wiederaufnahme, Abwehr von Pfad-Ausbrüchen |
| `test_bibliothek.py` | Zugangsdaten je Seite (Verschlüsselung, Subdomain-Treffer), Rubriken im Verlauf, Einsortieren des Altbestands samt Kollisionsschutz, Entscheidung des Players, Zwischenspeicher (Grenze 0 = unbegrenzt) |
| `test_release_zip.py` | Welche `fah-downloader*.zip` gewinnt (höchste `(N)`, dann Zeit), Aufräumen, und ein Ganzlauf von `manage.sh` gegen ein nachgebautes docker: richtige ZIP, `--force-recreate`, geschützte Dateien |
| `test_ports.py` | Automatische Portwahl mit echten Sockets: freier Port bleibt, fremd belegter weicht aus, eigener Container wird erkannt, Kollision in der `.env`, Nachrücken nach verlorenem Rennen |
| `test_cancel.py` | Zeigt im Direktvergleich, warum ein Abbruch mit eigener Exception-Klasse von yt-dlp verschluckt wird und mit `DownloadCancelled` durchkommt |

`test_engine.py` schaltet Metadaten-/Thumbnail-Einbettung ab, weil dafür ffmpeg
nötig wäre. Im Container ist ffmpeg vorhanden. Außerdem setzt es
`DOWNLOAD_ALLOW_PRIVATE=1`, weil sein Testserver auf `127.0.0.1` lauscht – ohne
den Schalter lehnt der Downloader solche Ziele seit 2.5.1 ab.

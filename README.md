# FAH Media Downloader

Ein selbst gehosteter Medien-Downloader im Stil von MeTube – aber **leistungs­stärker**
und für **alle Seiten** (yt-dlp, >1800 Portale: YouTube, Vimeo, TikTok, Twitter/X,
Instagram, Twitch, SoundCloud, Facebook u. v. m.).

## Features
- **Web-GUI** mit Live-Fortschritt (WebSocket), Warteschlange und **Bibliothek**
- **Bibliothek mit Rubriken**: ✓ Fertig · ✗ Fehlgeschlagen · ⏹ Abgebrochen –
  jeweils mit Suche, Einzel-Löschung und **Wiederaufnahme**
- **Zusätzlich nach Herkunft filtern**: Youtube · Musik · Video-Portale ·
  Mediatheken · Soziale Netze · Erwachsene · Sonstiges
- **Fertige Downloads direkt ansehen** – eingebauter Player mit Spulen
  (HTTP-Range-Streaming), alternativ herunterladen. **Auch MKV & Co.**: was der
  Browser nicht kann, wird vorher verlustfrei umgepackt statt endlos zu laden
- **Zielformat frei wählbar**: MKV, MP4, WebM, MOV – **verlustfrei umgepackt**,
  es wird nicht neu kodiert
- **Zuverlässiger Abbruch** inklusive Zwischenstatus; der Teil-Download bleibt
  liegen und wird bei der Wiederaufnahme fortgesetzt
- **Parallele Downloads** + Multi-Fragment pro Job (schneller als MeTube)
- **Alle Formate**: bestes Video, reines Audio (MP3/M4A/OPUS/FLAC/WAV), eigenes yt-dlp-Format
- **Ganze Liste oder einzelner Titel** – pro Download wählbar, nicht mehr geraten
- **Qualitäts-Cap** (bis 4K), Playlists, Untertitel, Thumbnails/Metadaten, **SponsorBlock**
- **Zugangsdaten je Seite**: Domain + Benutzer + Passwort hinterlegen, der
  Downloader meldet sich selbst an (Cookie-Datei nur noch für Youtube nötig)
- **Ordnet automatisch ein**: `Quelle/Art/Jahr/Monat`, z. B. `Youtube/video/2026/09`
- **Ziel frei wählbar**: Ordner-Browser über den eingebundenen **Medien-Teilbaum des Hosts**
  (`HOSTFS_MOUNT`, optional weiter eingeschränkt per `DOWNLOAD_ROOTS`)
- **NAS-/Server-Freigaben** (SMB/CIFS) direkt in der GUI einbinden – mit **NAS-Login**
- **Job-Protokoll** pro Download: bei einem Fehlschlag steht die echte Ursache da
- **Login-geschützte GUI** (Admin-Konto beim ersten Start), PWA-fähig mit eigenem Icon
- **yt-dlp aktualisiert sich selbst** (höchstens einmal täglich, mit Zeitlimit)
- **Browser-Tarnung** (seit 2.5.2): Seiten, die Programme mit „HTTP 403 Forbidden“ abweisen (PornHub, Cloudflare-Portale), bekommen automatisch einen zweiten, als Chrome getarnten Versuch
- **Zeitlimit je Auftrag** (Standard 6 h): ein eingeschlafener Download blockiert keinen Platz mehr, der Teil-Download bleibt für „Fortsetzen“ liegen
- **Nur http(s)-Quellen, nichts aus dem eigenen Netz**: Adressen wie `10.x`, `192.168.x`, `127.x`, `localhost` werden abgelehnt (per `DOWNLOAD_ALLOW_PRIVATE=1` freischaltbar)
- **Update per `manage.sh`**: neue `fah-downloader.zip` ins Verzeichnis legen → fertig; `fah-downloader (1).zip` gilt als neuer als `fah-downloader.zip`, der Container wird mit `--force-recreate` ersetzt
- **Persistente Daten** (`config/`, `downloads/`) werden von Updates **nie** überschrieben

## Schnellstart (Erstinstallation)

`unzip` ist nicht nötig – nur `python3` und `docker`.

```sh
# 1) fah-downloader.zip ins gewünschte Verzeichnis legen, dann:
python3 -c "import zipfile;zipfile.ZipFile('fah-downloader.zip').extract('manage.sh')"
chmod +x manage.sh

# 2) Installieren (baut Container, wählt freien Port, legt Configs an)
./manage.sh update
```

Am Ende zeigt das Script die WebGUI-URL. Beim ersten Öffnen legst du dein Admin-Konto an.

## Updaten
Neue `fah-downloader.zip` ins selbe Verzeichnis legen und:
```sh
./manage.sh update
```
- `manage.sh` aktualisiert sich selbst aus der ZIP.
- Nur Code wird ersetzt; `config/`, `downloads/`, `.env`, `docker-compose.yml` bleiben.
- Der einmal vergebene Port bleibt fix.
- Verlaufsdatenbank und Einstellungen werden **automatisch migriert**.
- **Welche ZIP gewinnt?** Browser hängen beim erneuten Herunterladen ` (1)`,
  ` (2)` … an. Die **höchste Nummer** ist der jüngste Download und wird
  bevorzugt – unabhängig von der Änderungszeit, denn die verschiebt sich schon
  beim Kopieren. Nach dem Einspielen räumt `manage.sh` die älteren Fassungen
  weg; eine ausdrücklich angegebene Datei (`./manage.sh update meine.zip`)
  bleibt immer liegen.
- Der Container wird bei jedem Update mit **`--force-recreate`** ersetzt.
  `up -d` allein ließe ihn stehen, wenn compose keine Änderung erkennt – neue
  Funktionen aus der ZIP kämen dann erst beim nächsten Zufall an.

Automatisch bei neuer ZIP deployen:
```sh
./manage.sh watch &
```

## Weitere Befehle
| Befehl | Wirkung |
|---|---|
| `./manage.sh start` / `stop` / `restart` | Container steuern |
| `./manage.sh status` | Container-Status |
| `./manage.sh logs` | Logs folgen |
| `./manage.sh url` | WebGUI-URL anzeigen |
| `./manage.sh port` / `port 9000` | Port zeigen / ändern |
| `./manage.sh update --no-cache` | vollständiger Rebuild |

## Bedienung

### Zielformat (Container)
Im Download-Tab neben dem Modus. `automatisch` behält, was die Quelle liefert.
`MKV` ist der sicherste Container (nimmt jeden Codec), `MP4` der kompatibelste.
Das Umpacken ist **verlustfrei** und dauert Sekunden – es wird nicht neu kodiert.

> Bei `MP4` wird zusätzlich bevorzugt eine Quelle gewählt, die schon MP4-kompatibel
> ist. Passt ein Codec nicht in den Zielcontainer (z. B. VP9 in MOV), behält
> yt-dlp den Originalcontainer bei, statt die Datei neu zu kodieren.

Ein Standardwert lässt sich unter *Einstellungen → Standard-Zielformat* setzen.

### Ablage in Unterordnern
Neue Downloads werden im Zielordner automatisch einsortiert:

```
<Zielordner>/
└── Youtube/           ← Quelle (aus der URL: Youtube, Vimeo, ARD, …)
    ├── video/         ← Art (video oder audio)
    │   └── 2026/
    │       └── 09/    ← Jahr und Monat des Downloads
    └── audio/
        └── 2026/09/
```

Bekannte Plattformen bekommen einen sprechenden Namen und fassen ihre
Schreibweisen zusammen: `youtu.be`, `m.youtube.com` und `www.youtube.com` landen
alle unter `Youtube`. `music.youtube.com` bleibt bewusst getrennt (`YT-Musik`).
Jede andere Seite behält ihren Host ohne das reine Darstellungs-Präfix
(`www.beispiel-seite.de` → `beispiel-seite.de`). yt-dlp-Suchanfragen landen unter
`Suche`, alles ohne verwertbaren Host unter `Sonstige`. Ordnernamen werden so
gefiltert, dass sie auch auf SMB-Freigaben und unter Windows anlegbar sind.

Abschalten unter *Einstellungen → „Unterordner automatisch anlegen"* – dann
landet alles wieder flach im Zielordner.

**Altbestand nachträglich einsortieren:** *Einstellungen → Ablage &
Zwischenspeicher → Trockenlauf*. Er listet auf, welche Datei wohin wandern
würde – verschoben wird erst mit *Jetzt einsortieren*. Jahr und Monat stammen
dabei aus dem **Zeitpunkt des Downloads**, nicht aus dem heutigen Datum. Liegt
am Ziel schon eine gleichnamige Datei, bleibt alles, wie es ist; überschrieben
wird nie. Die Verlaufseinträge werden mitgezogen, damit Ansehen, Herunterladen und Löschen
weiter funktionieren. Einträge aus der Zeit **vor**
diesem Update laden bei ▶ Fortsetzen weiterhin flach in den Zielordner – dort
liegt ihre `.part`-Datei, und nur dort lässt sie sich fortsetzen.

> Jahr und Monat richten sich nach der Zeitzone des Containers – siehe `TZ`
> in der [`.env`](#konfiguration-env).

### Ganze Liste oder nur der eine Titel
Ein Link aus einer Liste sieht so aus: `…/watch?v=ABC&list=PL123`. Er ist beides
– ein einzelner Titel **und** ein Einstieg in eine Liste. Bisher wurde immer die
ganze Liste geladen; wer nur den angeklickten Titel wollte, musste den
`&list=`-Teil von Hand aus der URL löschen.

„Titel" heißt hier bewusst nicht „Video": bei `music.youtube.com` ist es ein
Lied, im Nur-Audio-Modus ebenfalls.

Im Download-Tab steht dafür jetzt **Playlist**:

| Auswahl | Wirkung |
|---|---|
| `ganze Liste laden` (Standard) | Alle Einträge der Playlist – wie bisher |
| `nur diesen Titel` | Ausschließlich der verlinkte Titel, der `&list=`-Teil wird ignoriert |

Die Vorgabe für neue Downloads steht unter *Einstellungen → Playlist-Vorgabe*,
im Download-Tab lässt sie sich pro Auftrag überstimmen. *Playlist-Items*
(z. B. `1-10`) wirkt nur, wenn die ganze Liste geladen wird.

> Zeigt die URL auf eine **reine** Listen-Seite (`…/playlist?list=…` oder ein
> Album bei YT-Musik), gibt es keinen einzelnen Titel, auf den sich „nur diesen
> Titel" beziehen könnte – dann lädt yt-dlp die Liste, unabhängig von dieser
> Auswahl.

### Ansehen im Browser
Ein Browser spielt **kein MKV** ab – genau der Container, der beim Download am
robustesten ist. Statt eines schwarzen Players, der die Datei stumpf lädt,
packt der Server sie vorher um: Bild wird Byte für Byte übernommen (`-c copy`),
nur die Hülle wechselt nach MP4 bzw. WebM. Das dauert Sekunden statt Stunden
und kostet keine Qualität. Nur eine Tonspur, die in der Zielhülle nicht erlaubt
ist (AC3, DTS, TrueHD), wird – und ausschließlich sie – nach AAC bzw. Opus
gewandelt.

Das Ergebnis liegt als Zwischendatei in `config/playcache`. Erst dadurch lässt
sich im Player **spulen**: ein direkt durchgereichter ffmpeg-Strom kann keine
Range-Anfragen beantworten. Der Zwischenspeicher räumt sich selbst auf, älteste
Datei zuerst; die Obergrenze steht in den Einstellungen (Standard 4 GB) und
lässt sich dort auch von Hand leeren.

Kann der Browser die **Bildspur** nicht darstellen (HEVC, MPEG-4, WMV), wäre
das keine Umpackung mehr, sondern echtes Neukodieren – dann sagt die Oberfläche
das offen und bietet den Download an.

### Browser-Tarnung (HTTP 403)

Manche Portale – PornHub ist das bekannteste Beispiel, dazu alles hinter
Cloudflare – erkennen an der TLS-/HTTP-Signatur, dass kein Browser anfragt,
und antworten mit `HTTP Error 403: Forbidden`, obwohl die Seite im Browser
läuft. yt-dlp kann sich dagegen über `curl_cffi` als Chrome ausgeben; das
Paket ist seit 2.5.2 fest im Image (ohne es ignoriert yt-dlp sogar die
Tarnung, die der jeweilige Extraktor selbst anfordert – genau daran scheiterte
PornHub bis 2.5.1).

Einstellung *Browser-Tarnung* (Einstellungen → mit Erklärtext):

| Wert | Verhalten |
|---|---|
| `auto` (Standard) | yt-dlp tarnt sich, wo die Seite es verlangt. Wird ein Auftrag trotzdem mit 403 abgewiesen, läuft er **einmal automatisch als Chrome getarnt** nach – das Protokoll des Auftrags zeigt die Wiederholung. |
| `immer` | Jede Anfrage getarnt. Hilft bei unbekannten Sperren, kann Tempo kosten. |
| `aus` | Keine Wiederholung; 403 bleibt ein Fehler. |

Andere Fehler (404, privat, Geosperre) werden **nicht** wiederholt.

### Titelbild und Zwischenordner (seit 2.5.3)
yt-dlp kann ein Titelbild nur in MP3, M4A, OPUS/OGG, FLAC, MKV/MKA, MP4/M4V
und MOV einbetten. Bei **WAV** und **WEBM** lässt der Downloader es deshalb
weg (das Protokoll des Auftrags sagt es). Bis 2.5.2 wurde es trotzdem
angefordert: yt-dlp brach dann die Nachbearbeitung ab, **bevor** die fertige
Datei aus dem Zwischenordner (`config/tmp`, Einstellung *Lokal
zwischenspeichern*) ans Ziel wanderte. Der Verlauf meldete „fertig“, aber
Ansehen und Laden antworteten „Datei existiert nicht mehr“.

Bricht die Nachbearbeitung aus einem anderen Grund ab, holt der Downloader
das Verschieben selbst nach (nie überschreibend: aus `Titel.wav` wird bei
Belegung `Titel (2).wav`) und schreibt den Grund in den Auftrag. Alte
Einträge dieser Art repariert er beim Start von selbst; abgebrochene Aufträge
bleiben dabei unberührt, ihre Reste braucht „Fortsetzen“.

### Zugangsdaten je Seite
*Einstellungen → Zugangsdaten je Seite*: Domain, Benutzername, Passwort. yt-dlp
meldet sich damit selbst an – das deckt Altersfreigaben, Abos und private
Kanäle bei den meisten Portalen ab. Ein Eintrag für `beispiel.de` gilt auch für
`www.beispiel.de` und jede Subdomain; ein genauerer Eintrag für
`kanal.beispiel.de` gewinnt gegen den allgemeinen.

Passwörter werden mit dem Instanz-Schlüssel (`config/secret.key`) verschlüsselt
in `config/logins.json` abgelegt und nie wieder angezeigt – die Oberfläche
erfährt nur, **dass** eines gesetzt ist. Wer nur den Benutzernamen korrigiert,
lässt das Passwortfeld leer; das gespeicherte bleibt dann stehen.

> **Youtube ist die Ausnahme:** Google lässt dort keine Passwort-Anmeldung von
> Fremdprogrammen zu. Dafür bleibt die Cookie-Datei der Weg – exportierbar mit
> einer **kostenlosen, quelloffenen** Erweiterung wie „Get cookies.txt LOCALLY"
> (Chrome/Edge) oder „cookies.txt" (Firefox). Ein kostenpflichtiges Add-on
> braucht es dafür nicht.

### Bibliothek
Drei Rubriken mit Zählern. Pro Eintrag je nach Zustand:

| Rubrik | Aktionen |
|---|---|
| ✓ Fertig | ▶ Ansehen · ⬇ Laden · 📋 Pfad · 🗑 Entfernen |
| ✗ Fehlgeschlagen | ↻ Erneut versuchen · 📄 Protokoll · 🗑 Entfernen |
| ⏹ Abgebrochen | ▶ Fortsetzen · 📄 Protokoll · 🗑 Entfernen |

Darunter filtert eine zweite Leiste nach **Herkunft** – Youtube, Musik,
Video-Portale, Mediatheken, Soziale Netze, Erwachsene, Sonstiges. Angezeigt
werden nur Rubriken, in denen auch etwas liegt. Bestehende Verlaufseinträge
werden beim ersten Start nach dem Update automatisch zugeordnet.

Beim Entfernen wird gefragt, ob **nur der Eintrag** oder **auch die Datei** weg soll.
„Rubrik leeren" entfernt ausschließlich Einträge – Dateien bleiben immer erhalten.

**▶ Fortsetzen** startet keinen neuen Download: der abgebrochene Teil liegt als
`.part`-Datei im Zielordner und wird per HTTP-Range fortgesetzt. Der beim
ersten Versuch gewählte Unterordner wird im Verlauf festgehalten, damit die
Wiederaufnahme ihn auch nach einem Monatswechsel wiederfindet.

### Ziel auf dem Host wählen
- **Lokaler Host**: `HOSTFS_MOUNT` aus der `.env` ist als `/hostfs` eingebunden –
  seit 2.5.1 standardmäßig **`/volume1`** statt des ganzen Host-Roots (der
  Container läuft als root; mit `/` als Ziel hätte jeder angemeldete Nutzer
  Schreibzugriff auf das komplette NAS). In der GUI unter *„Ziel → 📁 Wählen"*
  jeden Ordner darunter ansteuern (bzw. neu anlegen). Der Ordner-Browser nennt
  den erlaubten Bereich. Noch enger geht es mit `DOWNLOAD_ROOTS` (Komma-Liste
  von Unterordnern relativ zu `HOSTFS_MOUNT`, z. B. `medien/downloads,serien`):
  dann sind nur diese Teilbäume wählbar und beschreibbar.
- **NAS/Server**: Tab **„NAS / Ziel"** → Freigabe mit Server, Freigabename und
  NAS-Zugangsdaten einbinden. Danach im Ziel-Browser als `NAS: <name>` wählbar.
  Zugangsdaten liegen verschlüsselt in `config/shares.json` und werden beim
  Containerstart automatisch neu gemountet.

> **Tipp für NAS-Ziele:** *Einstellungen → „Lokal zwischenspeichern"* aktivieren.
> Dann laufen Fragment-Downloads und der ffmpeg-Merge lokal, und nur das fertige
> Ergebnis geht einmal übers Netz statt dreimal. Braucht entsprechend
> Plattenplatz in `config/tmp`.

## Ports – es gibt nie einen Konflikt

Genau **ein** Port wird auf dem Host belegt: die WebGUI. Alles andere braucht
keinen – `ffmpeg` und `yt-dlp` bauen ausschließlich **ausgehende** Verbindungen
auf und lauschen auf nichts. Im Container lauscht einzig `uvicorn`, und der
liegt in einem eigenen Netzwerk-Namensraum, kann also mit dem Host oder anderen
Containern gar nicht kollidieren.

Um den einen Host-Port kümmert sich `manage.sh` selbst:

| Wann | Was passiert |
|---|---|
| Erstinstallation | Freier Port per echtem TCP-Bind gesucht, in `.env` geschrieben, per `.downloader_ports.lock` fixiert – damit die URL stabil bleibt |
| **Vor jedem Start** (`update`, `start`, `restart`, `port`) | Ist der Port noch frei oder gehört er unserem eigenen Container? Wenn ja: unverändert lassen. Wenn ihn **jemand anderes** hält: nächsten freien nehmen und `.env` anpassen |
| Der Port wird zwischen Prüfung und Start weggeschnappt | `docker compose up` scheitert, `manage.sh` erkennt die Meldung, rückt einen Port weiter und startet erneut |

Ausgewichen wird also **nur bei echtem Fremdbesitz**. Der Bind-Test allein
würde nicht reichen: Läuft unser eigener Container, meldet er „belegt" – der
Dienst wäre vor sich selbst geflohen. Umgekehrt reicht `docker ps` allein auch
nicht, denn ein Dienst, der direkt auf dem NAS läuft, taucht dort nicht auf.
Deshalb beides.

Wechselt der Port, steht die neue Adresse am Ende der Ausgabe; `./manage.sh url`
zeigt sie jederzeit. Einen festen Port erzwingst du mit `./manage.sh port 8099`
– auch der weicht nur aus, wenn er fremd belegt ist.

> **Für später:** Braucht eine neue Funktion einmal einen eigenen Host-Port,
> genügt ein Eintrag in `PORT_VARS` (oben in `manage.sh`, Format
> `NAME:STANDARD`). Suche, Ausweichen und `.env`-Pflege gelten dann automatisch
> auch für ihn.

## Konfiguration (`.env`)
| Variable | Bedeutung | Default |
|---|---|---|
| `DOWNLOADER_PORT` | WebGUI-Port (autom. gewählt) | 8080 |
| `ADMIN_USER` / `ADMIN_PASSWORD` | optionaler Start-Admin | leer |
| `HOSTFS_MOUNT` | Host-Pfad als frei wählbares Ziel (Medien-Teilbaum, nicht `/`) | `/volume1` |
| `DOWNLOAD_ROOTS` | Komma-Liste erlaubter Unterordner von `HOSTFS_MOUNT`; leer = ganzer Mount | leer |
| `DOWNLOAD_ALLOW_PRIVATE` | `1` = Quellen im eigenen Netz (10.x, 192.168.x, localhost …) zulassen | `0` |
| `DOWNLOADS_HOST_PATH` | Standard-Downloadordner am Host | `./downloads` |
| `TZ` | Zeitzone | `Europe/Berlin` |

> Nach einem Update ergänzt `manage.sh` nur **fehlende Dienste** in der
> `docker-compose.yml`, nie Felder eines bestehenden Dienstes. Die neuen
> `environment`-Zeilen (`HOSTFS_HOST_PATH`, `DOWNLOAD_ROOTS`,
> `DOWNLOAD_ALLOW_PRIVATE`) und der geänderte `/hostfs`-Standard müssen auf
> einer bestehenden Installation **einmal von Hand** aus
> `docker-compose.yml.example` übernommen werden.

## Tests
Ohne Docker und ohne Internetzugang ausführbar – siehe [`tests/README.md`](tests/README.md).

## Icons
Alle Browser-/PWA-Icons entstehen aus einer einzigen Vektor-Definition:
```sh
python3 tools/make_icons.py
```
Kein Pillow, kein ffmpeg – nur die Standardbibliothek.

## Hinweis zu NAS-Mounts
Für SMB/CIFS-Mounts braucht der Container `CAP_SYS_ADMIN` + `CAP_DAC_READ_SEARCH`
(verlangt `mount.cifs`) **und** `apparmor:unconfined`: Dockers Standardprofil
`docker-default` enthält `deny mount` und blockt `mount(2)` auch mit
`SYS_ADMIN`. Alles drei steht in `docker-compose.yml.example`. Wer keine
Freigaben aus der Oberfläche einbindet, kann `cap_add`, `security_opt` und
`devices` dort entfernen – Downloads nach `/hostfs` und `/downloads` brauchen
nichts davon. Auf manchen NAS muss `cifs` im Kernel verfügbar sein.

Die Zugangsdaten gehen seit 2.5.1 über eine temporäre `credentials=`-Datei
(0600, danach gelöscht) an `mount.cifs` – nie mehr über die Optionszeile, die
in der Prozessliste und in jeder Fehlermeldung sichtbar wäre.

---
© 2026 Fabian Hammer – Alle Rechte vorbehalten.

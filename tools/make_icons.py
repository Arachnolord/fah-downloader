# ============================================================================
# Erdacht und programmiert von Fabian Hammer.
# (c) 2026 Fabian Hammer - Alle Rechte vorbehalten. Geistiges Eigentum.
# ============================================================================
"""Erzeugt saemtliche Browser-Icons aus einer einzigen Vektor-Definition.

Bewusst OHNE Pillow/ffmpeg/rsvg -- nur zlib+struct aus der Standardbibliothek,
damit das Skript auf jedem NAS und in jedem Build-Container laeuft.

    python3 tools/make_icons.py

Ergebnis in app/static/:
    favicon.svg          Vektor (moderne Browser, gestochen scharf)
    favicon.ico          16+32+48 px gebuendelt (Legacy-Fallback)
    favicon-16.png       Tab-Icon klein
    favicon-32.png       Tab-Icon / Lesezeichen
    apple-touch-icon.png 180 px (iOS-Homescreen)
    icon-192.png         PWA
    icon-512.png         PWA / Splash
    icon-maskable-512.png PWA mit Safe-Zone-Rand (Android adaptive icons)

Motiv: abgerundete Kachel im Marken-Blau mit weissem Download-Pfeil auf Ablage.
Bei 16 px bleibt der Pfeil lesbar, weil Schaft und Ablage bewusst fett sind.
"""
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "app" / "static"

# ── Markenfarben (identisch zu style.css) ───────────────────────────────────
BLUE_TOP = (0x5A, 0x9B, 0xFA)      # heller Verlaufsbeginn
BLUE_BOT = (0x25, 0x63, 0xEB)      # --pri2
WHITE = (0xFF, 0xFF, 0xFF)

SS = 4                              # Supersampling-Faktor fuer Kantenglaettung


# ── Geometrie in normalisierten Koordinaten (0..1) ──────────────────────────
def _in_rounded_rect(x, y, x0, y0, x1, y1, r):
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    # Eckbereiche per Kreisradius pruefen
    cx = x0 + r if x < x0 + r else (x1 - r if x > x1 - r else x)
    cy = y0 + r if y < y0 + r else (y1 - r if y > y1 - r else y)
    if cx == x and cy == y:
        return True
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _in_triangle(x, y, a, b, c):
    def sign(p, q, r):
        return (p[0] - r[0]) * (q[1] - r[1]) - (q[0] - r[0]) * (p[1] - r[1])
    d1, d2, d3 = sign((x, y), a, b), sign((x, y), b, c), sign((x, y), c, a)
    neg = d1 < 0 or d2 < 0 or d3 < 0
    pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (neg and pos)


def _glyph(x, y, inset=0.0):
    """Weisser Download-Pfeil. inset schrumpft das Motiv (Maskable-Safe-Zone)."""
    if inset:
        s = 1.0 - 2 * inset
        x = (x - inset) / s
        y = (y - inset) / s
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            return False
    # Schaft
    if _in_rounded_rect(x, y, 0.415, 0.155, 0.585, 0.520, 0.028):
        return True
    # Pfeilspitze
    if _in_triangle(x, y, (0.500, 0.700), (0.268, 0.455), (0.732, 0.455)):
        return True
    # Ablage
    if _in_rounded_rect(x, y, 0.230, 0.775, 0.770, 0.880, 0.045):
        return True
    return False


def _tile(x, y, inset=0.0):
    if inset:
        s = 1.0 - 2 * inset
        x = (x - inset) / s
        y = (y - inset) / s
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            return False
    return _in_rounded_rect(x, y, 0.0, 0.0, 1.0, 1.0, 0.225)


def render(size, inset=0.0, square=False):
    """Rastert das Motiv nach RGBA-Bytes (Supersampling gegen Treppchen)."""
    px = bytearray(size * size * 4)
    n = SS * SS
    for py in range(size):
        for pxx in range(size):
            tile_hits = glyph_hits = 0
            for sy in range(SS):
                for sx in range(SS):
                    u = (pxx + (sx + 0.5) / SS) / size
                    v = (py + (sy + 0.5) / SS) / size
                    if square or _tile(u, v, inset):
                        tile_hits += 1
                        if _glyph(u, v, inset):
                            glyph_hits += 1
            if not tile_hits:
                continue
            t = py / max(size - 1, 1)
            base = tuple(round(BLUE_TOP[i] + (BLUE_BOT[i] - BLUE_TOP[i]) * t)
                         for i in range(3))
            g = glyph_hits / n
            rgb = tuple(round(base[i] + (WHITE[i] - base[i]) * g) for i in range(3))
            o = (py * size + pxx) * 4
            px[o:o + 4] = bytes((*rgb, round(255 * tile_hits / n)))
    return bytes(px)


# ── PNG schreiben (reines zlib/struct) ──────────────────────────────────────
def _chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def png_bytes(size, rgba):
    raw = b"".join(b"\x00" + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 9))
            + _chunk(b"IEND", b""))


def ico_bytes(pngs):
    """ICO-Container mit PNG-Payload (von allen aktuellen Browsern gelesen)."""
    head = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, blobs = b"", b""
    for size, blob in pngs:
        entries += struct.pack("<BBBBHHII", size & 0xFF, size & 0xFF, 0, 0,
                               1, 32, len(blob), offset)
        offset += len(blob)
        blobs += blob
    return head + entries + blobs


SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" role="img" aria-label="FAH Downloader">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#5a9bfa"/>
      <stop offset="1" stop-color="#2563eb"/>
    </linearGradient>
  </defs>
  <rect width="100" height="100" rx="22.5" fill="url(#g)"/>
  <path fill="#fff" d="M41.5 15.5h17a2.8 2.8 0 0 1 2.8 2.8v27.2h11.9L50 70 26.8 45.5h11.9V18.3a2.8 2.8 0 0 1 2.8-2.8z"/>
  <rect x="23" y="77.5" width="54" height="10.5" rx="4.5" fill="#fff"/>
</svg>
"""

MANIFEST = """{
  "name": "FAH Media Downloader",
  "short_name": "FAH Downloader",
  "description": "Selbst gehosteter Medien-Downloader fuer ueber 1800 Portale.",
  "start_url": "/",
  "scope": "/",
  "display": "standalone",
  "orientation": "any",
  "background_color": "#0f1420",
  "theme_color": "#0f1420",
  "icons": [
    {"src": "/static/favicon-32.png",  "sizes": "32x32",   "type": "image/png"},
    {"src": "/static/icon-192.png",    "sizes": "192x192", "type": "image/png"},
    {"src": "/static/icon-512.png",    "sizes": "512x512", "type": "image/png"},
    {"src": "/static/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    {"src": "/static/favicon.svg",     "sizes": "any",     "type": "image/svg+xml"}
  ]
}
"""


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "favicon.svg").write_text(SVG, encoding="utf-8")
    (OUT / "site.webmanifest").write_text(MANIFEST, encoding="utf-8")

    cache = {}
    for size, name in ((16, "favicon-16.png"), (32, "favicon-32.png"),
                       (48, None), (180, "apple-touch-icon.png"),
                       (192, "icon-192.png"), (512, "icon-512.png")):
        blob = png_bytes(size, render(size))
        cache[size] = blob
        if name:
            (OUT / name).write_bytes(blob)
            print(f"  {name:<26} {len(blob):>7} B")

    # Android adaptive icons beschneiden die Ecken -> Motiv mit Rand rendern.
    mask = png_bytes(512, render(512, inset=0.10, square=True))
    (OUT / "icon-maskable-512.png").write_bytes(mask)
    print(f"  {'icon-maskable-512.png':<26} {len(mask):>7} B")

    ico = ico_bytes([(s, cache[s]) for s in (16, 32, 48)])
    (OUT / "favicon.ico").write_bytes(ico)
    print(f"  {'favicon.ico':<26} {len(ico):>7} B")
    print(f"  {'favicon.svg':<26} {len(SVG):>7} B")
    print(f"  {'site.webmanifest':<26} {len(MANIFEST):>7} B")


if __name__ == "__main__":
    main()

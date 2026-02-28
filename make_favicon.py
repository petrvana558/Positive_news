#!/usr/bin/env python3
"""Generuje static/favicon.ico – psí hlava Border Kolie, 32x32 RGBA.
Bez externích závislostí (pouze stdlib: struct, zlib).
Spusť jednou: python make_favicon.py
"""
import struct, zlib, os

def _chunk(tag, data):
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

def make_png(rows):
    """rows: seznam řádků, každý řádek je seznam (r,g,b,a) tuplů."""
    h, w = len(rows), len(rows[0])
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    raw  = b"".join(b"\x00" + bytes(v for px in row for v in px) for row in rows)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )

def wrap_ico(png_bytes, size):
    """Zabalí PNG do ICO formátu (jeden frame)."""
    ico_header = struct.pack("<HHH", 0, 1, 1)           # reserved, type=ICO, count=1
    entry      = struct.pack("<BBBBHHII",
        size, size, 0, 0, 1, 32,                        # w, h, colors, res, planes, bits
        len(png_bytes), 6 + 16                           # size, offset (header+entry)
    )
    return ico_header + entry + png_bytes

# ── Pixelová mapa 32×32 – psí hlava ──────────────────────────────────────────
S  = 32
cx = cy = S // 2

TRANSPARENT = (0,   0,   0,   0  )
BG          = (255, 248, 225, 255)   # #FFF8E1 teplé pozadí
HEAD        = (28,  28,  28,  255)   # #1C1C1C černá hlava
BLAZE       = (240, 240, 240, 255)   # #F0F0F0 bílá lyska
MUZZLE      = (200, 200, 200, 255)   # #C8C8C8 šedý čumák
IRIS        = (160, 120, 32,  255)   # #A07820 jantarová duhovka
PUPIL       = (13,  13,  13,  255)   # zornice / nos
EAR         = (28,  28,  28,  255)   # ucho (stejné jako hlava)
EAR_FOLD    = (42,  42,  42,  255)   # přeložená špička ucha

rows = []
for y in range(S):
    row = []
    for x in range(S):
        dx, dy = x - cx, y - cy
        dist = (dx*dx + dy*dy) ** 0.5

        # ── vnější kruh (roh = průhledný) ──
        if dist > 15.5:
            row.append(TRANSPARENT); continue

        px = BG  # výchozí: teplé pozadí

        # ── levé ucho: přibližně polygon (7,14)→(6,4)→(11,2)→(13,9) ──
        # zjednodušeno: pokud nahoře vlevo a mimo hlavu
        in_left_ear  = (x < 14 and y < 14 and dist > 11 and x > 5)
        in_right_ear = (x > 18 and y < 14 and dist > 11 and x < 27)

        if in_left_ear or in_right_ear:
            px = EAR
            # přeložená špička (horní třetina ucha)
            if y < 7:
                px = EAR_FOLD

        # ── černá hlava ──
        if dist < 13 and y < 27:
            px = HEAD

            # bílá lyska (vertikální pruh středem)
            if abs(dx) < 3 and 7 < y < 21:
                px = BLAZE

            # levé oko  (cx−5, cy−2) = (11, 14)
            lex, ley = 11, 15
            if (x-lex)**2 + (y-ley)**2 <= 5:  px = IRIS
            if (x-lex)**2 + (y-ley)**2 <= 2:  px = PUPIL
            if x == lex+1 and y == ley-1:      px = (255, 255, 255, 255)  # třpyt

            # pravé oko (cx+5, cy−2) = (21, 14)
            rex, rey = 21, 15
            if (x-rex)**2 + (y-rey)**2 <= 5:  px = IRIS
            if (x-rex)**2 + (y-rey)**2 <= 2:  px = PUPIL
            if x == rex+1 and y == rey-1:      px = (255, 255, 255, 255)

            # obočí (jantarová tečka nad okem)
            if y == 12 and abs(x - lex) <= 2:  px = IRIS
            if y == 12 and abs(x - rex) <= 2:  px = IRIS

        # ── šedý čumák ──
        if dx*dx * 0.35 + (y-24)**2 < 14 and dist < 13:
            px = MUZZLE

        # ── nos ──
        if dx*dx * 0.5 + (y-23)**2 < 3:
            px = PUPIL

        row.append(px)
    rows.append(row)

# ── Zápis ────────────────────────────────────────────────────────────────────
out = os.path.join(os.path.dirname(__file__), "static", "favicon.ico")
ico = wrap_ico(make_png(rows), S)
with open(out, "wb") as f:
    f.write(ico)
print(f"OK {out}  ({len(ico)} bytes)")

"""Generate the OG share image for /celebration (1200x630).

The page's aesthetic: a green-phosphor 80s CRT terminal cheerfully celebrating the
first trillionaire (fireworks, an escape rocket) — with the real, sourced human
cost rolling underneath as credits. The image mirrors that: gaudy phosphor
celebration up top, the somber credit line along the bottom.

Run:    venv/bin/python scripts/generate_celebration_og.py
Output: static/og-celebration.png
"""
import os
import math
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 1200, 630
BG       = (5, 7, 10)
GREEN    = (124, 252, 122)
DIMGREEN = (108, 170, 128)
FAINT    = (90, 130, 102)
CYAN     = (79, 209, 255)
AMBER    = (255, 211, 77)
PINK     = (255, 59, 107)
GOLD     = (255, 196, 60)
CREAM    = (247, 246, 236)


def mono(size, bold=False):
    try:
        return ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", size, index=1 if bold else 0)
    except Exception:
        return ImageFont.truetype("/System/Library/Fonts/Supplemental/Courier New.ttf", size)


img = Image.new("RGBA", (W, H), BG + (255,))

# soft phosphor glow, upper-left where the headline sits
glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
ImageDraw.Draw(glow).ellipse([-160, -240, 760, 320], fill=(38, 120, 60, 90))
img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(130)))

d = ImageDraw.Draw(img)


def glow_text(pos, text, font, fill, anchor="la", glow_rgb=None, blur=9):
    """Crisp phosphor text with an optional soft glow halo behind it."""
    if glow_rgb:
        gl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(gl).text(pos, text, font=font, fill=glow_rgb + (230,), anchor=anchor)
        img.alpha_composite(gl.filter(ImageFilter.GaussianBlur(blur)))
    d.text(pos, text, font=font, fill=fill + (255,), anchor=anchor)


# --- header prompt ---
glow_text((58, 44), "> PROSPERITY-OS v1.0  —  ONLINE", mono(22), DIMGREEN, glow_rgb=(40, 110, 60), blur=6)

# --- the headline (phosphor green, glowing) ---
hl = mono(82, bold=True)
glow_text((56, 122), "SEND ELON", hl, GREEN, glow_rgb=(40, 160, 70), blur=11)
glow_text((56, 214), "TO MARS.",  hl, GREEN, glow_rgb=(40, 160, 70), blur=11)
d.text((58, 320), "he reached a trillion — it's the least we can do.", font=mono(25), fill=DIMGREEN + (255,))
d.text((58, 356), "press the button. then read who paid for the trip.", font=mono(22), fill=FAINT + (255,))


# --- fireworks (upper-right sky) ---
def burst(cx, cy, color, r=30, n=14):
    soft = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(soft)
    for i in range(n):
        a = 2 * math.pi * i / n
        x, y = cx + math.cos(a) * r, cy + math.sin(a) * r
        sd.ellipse([x - 5, y - 5, x + 5, y + 5], fill=color + (255,))
    img.alpha_composite(soft.filter(ImageFilter.GaussianBlur(4)))
    for i in range(n):
        a = 2 * math.pi * i / n
        x, y = cx + math.cos(a) * r, cy + math.sin(a) * r
        d.ellipse([x - 2.5, y - 2.5, x + 2.5, y + 2.5], fill=color + (255,))
    d.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=color + (255,))


burst(900, 120, GOLD, r=34)
burst(1050, 210, PINK, r=26)
burst(770, 230, CYAN, r=22)
burst(1110, 95, GREEN, r=20)


# --- the escape rocket as a Jeff Koons balloon-dog (chrome + gold, glossy) ---
def soft_ellipse(bbox, rgba):
    """A translucent ellipse, alpha-composited (for gloss + candy reflections)."""
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(lay).ellipse(bbox, fill=rgba)
    img.alpha_composite(lay)

def rocket(cx, top):
    CHROME, CEDGE, GEDGE = (205, 213, 221), (108, 116, 124), (150, 108, 20)

    def gloss(bbox, fill, edge):
        d.ellipse(bbox, fill=fill, outline=edge, width=3)
        x0, y0, x1, y1 = bbox; w, h = x1 - x0, y1 - y0
        soft_ellipse([x0 + w * 0.16, y0 + h * 0.10, x0 + w * 0.50, y0 + h * 0.44], (255, 255, 255, 150))

    # exhaust flame
    d.polygon([(cx - 16, top + 210), (cx, top + 286), (cx + 16, top + 210)], fill=GOLD)
    d.polygon([(cx - 9, top + 210), (cx, top + 262), (cx + 9, top + 210)], fill=(255, 122, 26))
    # gold balloon 'legs' (fins)
    gloss([cx - 58, top + 182, cx - 22, top + 214], GOLD, GEDGE)
    gloss([cx + 22, top + 182, cx + 58, top + 214], GOLD, GEDGE)
    # lower chrome balloon segment
    gloss([cx - 46, top + 92, cx + 46, top + 208], CHROME, CEDGE)
    soft_ellipse([cx + 8, top + 110, cx + 32, top + 176], (255, 106, 213, 70))   # candy pink
    soft_ellipse([cx - 30, top + 118, cx - 10, top + 170], (94, 200, 255, 60))   # candy cyan
    # gold balloon knot (waist)
    gloss([cx - 22, top + 74, cx + 22, top + 98], GOLD, GEDGE)
    # upper chrome balloon segment
    gloss([cx - 36, top + 8, cx + 36, top + 96], CHROME, CEDGE)
    # gold balloon-dog ears + nose
    gloss([cx - 34, top - 22, cx - 12, top + 18], GOLD, GEDGE)
    gloss([cx + 12, top - 22, cx + 34, top + 18], GOLD, GEDGE)
    gloss([cx - 16, top - 18, cx + 16, top + 30], GOLD, GEDGE)
    # window port
    d.ellipse([cx - 3, top + 34, cx + 19, top + 56], fill=(127, 214, 255), outline=(202, 161, 58), width=3)
    soft_ellipse([cx + 1, top + 37, cx + 8, top + 44], (255, 255, 255, 200))
    # $ medallion
    d.ellipse([cx - 13, top + 130, cx + 13, top + 156], fill=GOLD, outline=GEDGE, width=2)
    d.text((cx, top + 143), "$", font=mono(20, bold=True), fill=(122, 92, 0), anchor="mm")


rocket(1010, 150)
glow_text((952, 250), "thanks, elon ↗", mono(22), AMBER, anchor="ra", glow_rgb=(120, 90, 20), blur=6)


# --- bottom: the somber credit twist ---
d.line([(58, 486), (1142, 486)], fill=(124, 252, 122, 70), width=1)
d.text((58, 506), "WHILE THE FIREWORKS ARE STILL WARM, THE CREDITS ROLL:",
       font=mono(21), fill=FAINT + (255,))
glow_text((58, 538), "USAID · PEPFAR · MALARIA · CHILD NUTRITION",
          mono(23, bold=True), GREEN, glow_rgb=(40, 130, 60), blur=6)
d.text((58, 572), "sourced, projected deaths from the funding cuts",
       font=mono(20), fill=FAINT + (255,))
d.text((1142, 576), "thetimecost.com/celebration", font=mono(23), fill=AMBER + (255,), anchor="ra")


# --- CRT scanlines + vignette over the whole thing ---
scan = Image.new("RGBA", (W, H), (0, 0, 0, 0))
sd = ImageDraw.Draw(scan)
for y in range(0, H, 3):
    sd.line([(0, y), (W, y)], fill=(0, 0, 0, 60), width=1)
img.alpha_composite(scan)

vig = Image.new("RGBA", (W, H), (0, 0, 0, 0))
ImageDraw.Draw(vig).rectangle([0, 0, W, H], outline=(0, 0, 0, 200), width=110)
img = Image.alpha_composite(img, vig.filter(ImageFilter.GaussianBlur(70)))

out = os.path.join(os.path.dirname(__file__), os.pardir, "static", "og-celebration.png")
img.convert("RGB").save(out, "PNG")
print("wrote", os.path.abspath(out))

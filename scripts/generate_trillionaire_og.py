"""Generate the Trillionaire Time OG share image (1200x630).

Warhol (pop-colour grocery prints + Ben-Day dots + offset-shadow type),
Engelbreit (checkerboard ribbon + script lettering + cherries) and
Caravaggio (dark, spotlit chiaroscuro stage).

Run: venv/bin/python scripts/generate_trillionaire_og.py
Output: static/og-trillionaire.png
"""
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 1200, 630
INK   = (20, 16, 12)
CREAM = (247, 236, 214)
GOLD  = (255, 207, 92)
PINK  = (255, 45, 149)
BLUE  = (45, 140, 255)
YELLOW= (255, 212, 0)

F = "/System/Library/Fonts/Supplemental/"
def impact(s): return ImageFont.truetype(F + "Impact.ttf", s)
def arial(s):  return ImageFont.truetype(F + "Arial.ttf", s)
def arialb(s): return ImageFont.truetype(F + "Arial Bold.ttf", s)
def script(s): return ImageFont.truetype(F + "SnellRoundhand.ttc", s)

# --- Caravaggio: dark stage + warm top spotlight ---
img = Image.new("RGBA", (W, H), (11, 8, 5, 255))
glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
gd = ImageDraw.Draw(glow)
gd.ellipse([180, -200, 1020, 380], fill=(255, 198, 108, 95))
glow = glow.filter(ImageFilter.GaussianBlur(135))
img = Image.alpha_composite(img, glow)
# subtle vignette
vig = Image.new("RGBA", (W, H), (0, 0, 0, 0))
ImageDraw.Draw(vig).rectangle([0, 0, W, H], outline=(0, 0, 0, 160), width=120)
img = Image.alpha_composite(img, vig.filter(ImageFilter.GaussianBlur(70)))
d = ImageDraw.Draw(img)

# --- Engelbreit eyebrow (script, gold) ---
d.text((W // 2, 34), "trillionaire time", font=script(48), fill=GOLD, anchor="ma")

# --- Warhol title with offset duotone shadow ---
def title(cx, y, text, size):
    f = impact(size)
    d.text((cx + 6, y + 7), text, font=f, fill=BLUE, anchor="ma")
    d.text((cx + 3, y + 3), text, font=f, fill=PINK, anchor="ma")
    d.text((cx, y), text, font=f, fill=(255, 246, 230), anchor="ma")

title(W // 2, 86, "YOU BUY MILK.", 84)
title(W // 2, 168, "THEY BUY THE BLOCK.", 84)

# --- Warhol pop tiles (3 groceries) ---
def bendots(x0, y0, x1, y1):
    for yy in range(y0 + 10, y1, 16):
        for xx in range(x0 + 10, x1, 16):
            d.ellipse([xx - 2, yy - 2, xx + 2, yy + 2], fill=(0, 0, 0, 45))

def milk(cx, cy):
    d.rounded_rectangle([cx - 38, cy - 18, cx + 38, cy + 56], radius=8, fill=CREAM, outline=INK, width=4)
    d.polygon([(cx - 38, cy - 18), (cx, cy - 54), (cx + 38, cy - 18)], fill=CREAM, outline=INK)
    d.line([(cx, cy - 54), (cx, cy - 18)], fill=INK, width=3)

def egg(cx, cy):
    d.ellipse([cx - 34, cy - 44, cx + 34, cy + 48], fill=CREAM, outline=INK, width=4)

def bread(cx, cy):
    d.rounded_rectangle([cx - 50, cy - 22, cx + 50, cy + 40], radius=26, fill=(232, 196, 122), outline=INK, width=4)
    for off in (-22, 0, 22):
        d.line([(cx + off - 6, cy - 14), (cx + off + 6, cy + 30)], fill=INK, width=3)

tiles = [(PINK, milk), (BLUE, egg), (YELLOW, bread)]
tw, gap = 200, 44
total = len(tiles) * tw + (len(tiles) - 1) * gap
x0 = (W - total) // 2
ty = 270
for i, (color, icon) in enumerate(tiles):
    x = x0 + i * (tw + gap)
    d.rounded_rectangle([x, ty, x + tw, ty + tw], radius=20, fill=color, outline=INK, width=5)
    bendots(x, ty, x + tw, ty + tw)
    icon(x + tw // 2, ty + tw // 2)

# --- Engelbreit checkerboard ribbon ---
cs, ry = 18, ty + tw + 22
for i, x in enumerate(range(x0, x0 + total, cs)):
    d.rectangle([x, ry, x + cs, ry + cs], fill=(INK if i % 2 == 0 else CREAM))

# --- cherries (Engelbreit), tucked by the ribbon ---
def cherry(cx, cy):
    d.line([(cx, cy - 22), (cx + 10, cy - 34)], fill=(60, 120, 40), width=4)
    d.line([(cx + 18, cy - 20), (cx + 10, cy - 34)], fill=(60, 120, 40), width=4)
    d.ellipse([cx - 11, cy - 11, cx + 11, cy + 11], fill=(214, 38, 60), outline=INK, width=2)
    d.ellipse([cx + 7, cy - 9, cx + 29, cy + 13], fill=(214, 38, 60), outline=INK, width=2)
cherry(x0 - 6, ry + 6)

# --- punchline + footer ---
d.text((W // 2, ry + 34), "The minutes you work for a loaf of bread? They earn dozens of homes.",
       font=arialb(25), fill=CREAM, anchor="ma")
d.text((W // 2, ry + 74), "thetimecost.com / trillionaire", font=arial(22), fill=GOLD, anchor="ma")

out = os.path.join(os.path.dirname(__file__), os.pardir, "static", "og-trillionaire.png")
img.convert("RGB").save(out, "PNG")
print("wrote", os.path.abspath(out))

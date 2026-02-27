#!/usr/bin/env python3
"""
One-time script to generate the TimeCost OG image (1200x630).
Run:  pip install Pillow && python scripts/generate_og_image.py
Output: static/og-image.png
"""

import os, sys
from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
OUT = os.path.join(os.path.dirname(__file__), "..", "static", "og-image.png")

# --- colours (from favicon / brand) ---
BG_TOP = (31, 42, 51)       # #1f2a33
BG_BOT = (74, 124, 130)     # #4a7c82
GOLD   = (242, 210, 143)    # #f2d28f
WHITE  = (255, 255, 255)
MUTED  = (180, 195, 200)
CARD   = (38, 52, 62, 230)  # semi-transparent dark card

# --- gradient background ---
img = Image.new("RGB", (W, H))
for y in range(H):
    t = y / H
    r = int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * t)
    g = int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * t)
    b = int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * t)
    for x in range(W):
        img.putpixel((x, y), (r, g, b))

draw = ImageDraw.Draw(img, "RGBA")

# --- fonts (try system fonts, fall back to default) ---
def load_font(names, size):
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            pass
    return ImageFont.load_default()

font_bold_lg = load_font([
    "/System/Library/Fonts/Avenir Next.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "arial.ttf",
], 52)

font_bold_md = load_font([
    "/System/Library/Fonts/Avenir Next.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "arial.ttf",
], 36)

font_reg = load_font([
    "/System/Library/Fonts/Avenir Next.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "arial.ttf",
], 28)

font_sm = load_font([
    "/System/Library/Fonts/Avenir Next.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "arial.ttf",
], 22)

# --- draw rounded card background ---
card_x, card_y = 80, 80
card_w, card_h = W - 160, H - 160
card_r = 28
draw.rounded_rectangle(
    [card_x, card_y, card_x + card_w, card_y + card_h],
    radius=card_r,
    fill=CARD,
)

# --- TimeCost wordmark (top-left of card) ---
draw.text((card_x + 48, card_y + 40), "TimeCost", fill=GOLD, font=font_bold_md)

# --- gold accent line ---
line_y = card_y + 100
draw.line([(card_x + 48, line_y), (card_x + 200, line_y)], fill=GOLD, width=3)

# --- example calculation ---
calc_y = card_y + 140

# Item name
draw.text((card_x + 48, calc_y), "A week in Paris", fill=WHITE, font=font_bold_lg)

# Price = time (two-colour line)
arrow_y = calc_y + 72
price_text = "\u00a32,200  =  "
draw.text((card_x + 48, arrow_y), price_text, fill=GOLD, font=font_bold_md)
price_bbox = draw.textbbox((card_x + 48, arrow_y), price_text, font=font_bold_md)
draw.text((price_bbox[2], arrow_y), "146 hours of your life", fill=WHITE, font=font_bold_md)

# --- tagline ---
tag_y = card_y + card_h - 80
draw.text((card_x + 48, tag_y), "Time is the real currency", fill=MUTED, font=font_reg)

# --- domain (bottom-right) ---
domain_text = "thetimecost.com"
bbox = draw.textbbox((0, 0), domain_text, font=font_sm)
tw = bbox[2] - bbox[0]
draw.text((card_x + card_w - 48 - tw, tag_y + 6), domain_text, fill=GOLD, font=font_sm)

# --- save ---
img.save(OUT, "PNG", optimize=True)
print(f"OG image saved to {os.path.abspath(OUT)}")
print(f"Size: {os.path.getsize(OUT) / 1024:.0f} KB")

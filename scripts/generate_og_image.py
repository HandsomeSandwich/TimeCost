#!/usr/bin/env python3
"""
One-time script to generate the TimeCost OG image (1200x630).
Run:  pip install Pillow && python scripts/generate_og_image.py
Output: static/og-image.png
"""

import os, math
from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
OUT = os.path.join(os.path.dirname(__file__), "..", "static", "og-image.png")

# --- colours ---
BG_TOP = (24, 32, 38)       # dark navy top
BG_BOT = (42, 58, 68)       # slightly lighter bottom
GOLD   = (242, 210, 143)    # #f2d28f  brand accent
WHITE  = (255, 255, 255)
MUTED  = (160, 175, 185)
SOFT   = (120, 140, 150)
BAR_BG = (55, 72, 85)       # bar track
BAR_FG = (105, 165, 180)    # bar fill (teal)
BAR_YOU = (242, 210, 143)   # your bar (gold)

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

# --- faded clock background ---
# Large ghostly clock face in the upper-right
cx, cy = W - 220, H // 2 - 20   # centre of clock
radius = 260
clock_alpha = 18  # very faint

# Outer ring
for w in range(2):
    draw.ellipse(
        [cx - radius - w, cy - radius - w, cx + radius + w, cy + radius + w],
        outline=(*WHITE[:3], clock_alpha),
    )

# Hour marks
for h in range(12):
    angle = math.radians(h * 30 - 90)
    inner = radius - 20
    outer = radius - 6
    x1 = cx + int(inner * math.cos(angle))
    y1 = cy + int(inner * math.sin(angle))
    x2 = cx + int(outer * math.cos(angle))
    y2 = cy + int(outer * math.sin(angle))
    tick_w = 3 if h % 3 == 0 else 1
    draw.line([(x1, y1), (x2, y2)], fill=(*WHITE[:3], clock_alpha + 6), width=tick_w)

# Minute hand (pointing at ~10:10 for classic look)
min_angle = math.radians(60 - 90)  # 10-minute position
min_len = radius * 0.72
draw.line(
    [(cx, cy), (cx + int(min_len * math.cos(min_angle)), cy + int(min_len * math.sin(min_angle)))],
    fill=(*WHITE[:3], clock_alpha + 4), width=2,
)

# Hour hand (pointing at ~10)
hr_angle = math.radians(300 - 90)  # 10-o'clock position
hr_len = radius * 0.48
draw.line(
    [(cx, cy), (cx + int(hr_len * math.cos(hr_angle)), cy + int(hr_len * math.sin(hr_angle)))],
    fill=(*WHITE[:3], clock_alpha + 4), width=3,
)

# Centre dot
draw.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=(*WHITE[:3], clock_alpha + 8))

# --- fonts ---
def load_font(names, size):
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            pass
    return ImageFont.load_default()

FONT_PATHS = [
    "/System/Library/Fonts/Avenir Next.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "arial.ttf",
]
FONT_PATHS_REG = [
    "/System/Library/Fonts/Avenir Next.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "arial.ttf",
]

font_brand   = load_font(FONT_PATHS, 26)
font_title   = load_font(FONT_PATHS, 48)
font_price   = load_font(FONT_PATHS, 34)
font_label   = load_font(FONT_PATHS_REG, 20)
font_bar_lbl = load_font(FONT_PATHS_REG, 18)
font_tagline = load_font(FONT_PATHS_REG, 22)
font_sm      = load_font(FONT_PATHS_REG, 18)

# --- layout ---
LEFT = 72
RIGHT = W - 72

# TimeCost brand top-left
draw.text((LEFT, 42), "TimeCost", fill=GOLD, font=font_brand)

# Gold accent line
draw.line([(LEFT, 82), (LEFT + 120, 82)], fill=GOLD, width=2)

# Main example
draw.text((LEFT, 108), "A week in Paris", fill=WHITE, font=font_title)

price_text = "\u00a32,200  =  "
price_y = 172
draw.text((LEFT, price_y), price_text, fill=GOLD, font=font_price)
bbox = draw.textbbox((LEFT, price_y), price_text, font=font_price)
draw.text((bbox[2], price_y), "146 hours of your life", fill=WHITE, font=font_price)

# Divider
div_y = 228
draw.line([(LEFT, div_y), (RIGHT, div_y)], fill=(60, 78, 90), width=1)

# Wealth comparison section
comp_y = 250
draw.text((LEFT, comp_y), "While you work 146 hours, they work...", fill=MUTED, font=font_label)

# Bar chart data
bars = [
    ("Elon Musk",       "0.05s", 1.0),
    ("Jeff Bezos",      "0.09s", 0.40),
    ("Mark Zuckerberg", "0.10s", 0.53),
    ("Bill Gates",      "0.19s", 0.07),
    ("You",             "146h 40m", None),
]

bar_x = LEFT + 180
bar_right = RIGHT - 80
bar_w = bar_right - bar_x
bar_h = 24
bar_start_y = 290

for i, (name, time_str, pct) in enumerate(bars):
    y = bar_start_y + i * 52

    # Name label (right-aligned before bar)
    name_bbox = draw.textbbox((0, 0), name, font=font_bar_lbl)
    name_w = name_bbox[2] - name_bbox[0]
    draw.text((bar_x - 16 - name_w, y + 2), name, fill=MUTED if pct is not None else GOLD, font=font_bar_lbl)

    # Bar track
    draw.rounded_rectangle(
        [bar_x, y, bar_x + bar_w, y + bar_h],
        radius=4,
        fill=BAR_BG,
    )

    if pct is not None:
        # Filled bar
        fill_w = max(8, int(bar_w * pct))
        draw.rounded_rectangle(
            [bar_x, y, bar_x + fill_w, y + bar_h],
            radius=4,
            fill=BAR_FG,
        )
        # Time label after bar
        draw.text((bar_x + fill_w + 10, y + 1), time_str, fill=WHITE, font=font_bar_lbl)
    else:
        # "You" bar - tiny gold sliver
        draw.rounded_rectangle(
            [bar_x, y, bar_x + 4, y + bar_h],
            radius=2,
            fill=BAR_YOU,
        )
        draw.text((bar_x + 16, y + 1), time_str, fill=GOLD, font=font_bar_lbl)

# --- bottom bar ---
bot_y = H - 56
draw.line([(LEFT, bot_y - 16), (RIGHT, bot_y - 16)], fill=(60, 78, 90), width=1)
draw.text((LEFT, bot_y), "Time is the real currency", fill=SOFT, font=font_tagline)

domain = "thetimecost.com"
d_bbox = draw.textbbox((0, 0), domain, font=font_sm)
d_w = d_bbox[2] - d_bbox[0]
draw.text((RIGHT - d_w, bot_y + 4), domain, fill=GOLD, font=font_sm)

# --- save ---
img.save(OUT, "PNG", optimize=True)
print(f"OG image saved to {os.path.abspath(OUT)}")
print(f"Size: {os.path.getsize(OUT) / 1024:.0f} KB")

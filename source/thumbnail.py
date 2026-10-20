"""Workshop / launcher thumbnail for Military Drill (drawn, no artwork of anyone else).

    python thumbnail.py <out.png>

512 x 512 PNG. The launcher shows a picture that has the same name as the pack and lies next to it.
A view from above: a standing unit (blue) opens a lane, its models step aside, another unit (gold) walks through.
"""
import math
import random
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SIZE = 512
S = 3  # drawn 3 times as large, then scaled down (smooth edges)
FONT_TITLE = r'C:\Windows\Fonts\georgiab.ttf'
FONT_SMALL = r'C:\Windows\Fonts\seguisb.ttf'

GROUND_TOP, GROUND_BOTTOM = (58, 74, 44), (30, 40, 26)
BLUE, BLUE_DARK, BLUE_STEP = (92, 140, 196), (30, 52, 84), (150, 190, 232)
GOLD, GOLD_DARK = (232, 178, 62), (104, 66, 14)
PAPER, INK = (236, 226, 200), (38, 28, 18)


def disc(d, x, y, r, fill, edge, facing=None):
    d.ellipse([x - r + 3, y - r + 5, x + r + 3, y + r + 5], fill=(0, 0, 0, 70))  # shadow
    d.ellipse([x - r, y - r, x + r, y + r], fill=fill, outline=edge, width=max(2, r // 5))
    if facing is not None:  # a small mark where the model looks
        fx, fy = x + math.cos(facing) * r * 0.55, y + math.sin(facing) * r * 0.55
        d.ellipse([fx - r * 0.22, fy - r * 0.22, fx + r * 0.22, fy + r * 0.22], fill=edge)


def arrow(d, x0, y0, x1, y1, width, colour, head=2.6):
    a = math.atan2(y1 - y0, x1 - x0)
    hl = width * head
    bx, by = x1 - math.cos(a) * hl, y1 - math.sin(a) * hl
    d.line([x0, y0, bx, by], fill=colour, width=width)
    left = (bx + math.cos(a + math.pi / 2) * hl * 0.6, by + math.sin(a + math.pi / 2) * hl * 0.6)
    right = (bx + math.cos(a - math.pi / 2) * hl * 0.6, by + math.sin(a - math.pi / 2) * hl * 0.6)
    d.polygon([left, right, (x1, y1)], fill=colour)


def draw():
    w = SIZE * S
    rnd = random.Random(7)
    img = Image.new('RGB', (w, w))
    px = img.load()
    for y in range(w):  # ground: gradient with a little grain
        t = y / (w - 1)
        base = [GROUND_TOP[i] + (GROUND_BOTTOM[i] - GROUND_TOP[i]) * t for i in range(3)]
        for x in range(w):
            n = rnd.randint(-5, 5)
            px[x, y] = tuple(int(max(0, min(255, c + n))) for c in base)
    img = img.filter(ImageFilter.GaussianBlur(S * 0.6))
    over = Image.new('RGBA', (w, w), (0, 0, 0, 0))
    d = ImageDraw.Draw(over)

    # the standing unit: 7 ranks, 13 files, looking up the picture; the files next to the middle step aside
    files, ranks = 13, 7
    gap = 31 * S
    r = 10 * S
    left = w / 2 - (files - 1) * gap / 2
    top = 222 * S
    lane = 1.25 * gap  # half width of the lane the walkers use
    reach = 5  # files on each side that close up to make the lane
    stepping = []
    for rank in range(ranks):
        for f in range(files):
            x = left + f * gap
            y = top + rank * gap
            off = (x - w / 2) / gap
            if abs(off) < reach:  # the nearer to the lane, the further a model steps aside
                side = 1 if off > 0.01 or (abs(off) <= 0.01 and rank % 2) else -1
                nx = w / 2 + side * (lane + abs(off) * (reach * gap - lane) / reach) + rnd.uniform(-1.5, 1.5) * S
                ny = y + (rnd.uniform(-0.2, 0.2) * gap if abs(off) <= 0.01 else rnd.uniform(-1.5, 1.5) * S)
                moved = abs(nx - x) > gap * 0.4
                stepping.append((x, y, nx, ny))
                disc(d, nx, ny, r, BLUE_STEP if moved else BLUE, BLUE_DARK, -math.pi / 2)
            else:
                disc(d, x + rnd.uniform(-1, 1) * S, y + rnd.uniform(-1, 1) * S, r, BLUE, BLUE_DARK, -math.pi / 2)
    for x, y, nx, ny in stepping:  # small step arrows over some of those that moved most
        if abs(nx - x) > gap * 0.7 and rnd.random() < 0.6:
            side = 1 if nx > x else -1
            arrow(d, nx - side * r * 1.5, ny - r * 1.45, nx + side * r * 0.9, ny - r * 1.45, 2 * S, (255, 255, 255, 215), head=2.4)

    # the walking unit: a column two wide coming up through the lane
    head_y = top - 1.7 * gap
    for row in range(0, 12):
        for col in (-0.5, 0.5):
            x = w / 2 + col * gap * 0.95 + rnd.uniform(-2, 2) * S
            y = head_y + row * gap * 0.98 + rnd.uniform(-2, 2) * S
            if y < w - 58 * S:
                disc(d, x, y, r, GOLD, GOLD_DARK, -math.pi / 2)
    # the way they go
    arrow(d, w / 2, head_y - r * 1.6, w / 2, 112 * S, 10 * S, (255, 236, 170, 240), head=1.9)

    img = Image.alpha_composite(img.convert('RGBA'), over)

    # title band
    d = ImageDraw.Draw(img)
    band_h = 100 * S
    d.rectangle([0, 0, w, band_h], fill=PAPER)
    d.rectangle([0, band_h, w, band_h + 4 * S], fill=INK)
    title = ImageFont.truetype(FONT_TITLE, 44 * S)
    small = ImageFont.truetype(FONT_SMALL, 21 * S)
    for n, line in enumerate(('MILITARY', 'DRILL')):
        tw = d.textlength(line, font=title)
        d.text(((w - tw) / 2, (5 + n * 44) * S), line, font=title, fill=INK)
    # foot band
    foot_h = 46 * S
    d.rectangle([0, w - foot_h, w, w], fill=INK)
    text = 'standing friendly units give way'
    tw = d.textlength(text, font=small)
    d.text(((w - tw) / 2, w - foot_h + 8 * S), text, font=small, fill=PAPER)
    # frame
    d.rectangle([0, 0, w - 1, w - 1], outline=INK, width=5 * S)
    return img.convert('RGB').resize((SIZE, SIZE), Image.LANCZOS)


if __name__ == '__main__':
    out = sys.argv[1]
    picture = draw()
    picture.save(out, optimize=True)
    print('%s  %dx%d' % (out, picture.width, picture.height))

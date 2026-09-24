"""Preview sheet: the icon at 1024 / 180 / 60 / 20 on a busy mid-grey ground.
Small sizes are downsampled from the shipped 1024 PNG (what actool does), then given the
iOS rounded mask for display only. Magnified nearest-neighbour copies show the real pixels."""
import random
import sys

from PIL import Image, ImageDraw, ImageFont

src, out = sys.argv[1], sys.argv[2]
icon = Image.open(src).convert("RGB")
W, H = 1800, 1250
rnd = random.Random(7)


def font(sz, bold=False):
    try:
        return ImageFont.truetype("/System/Library/Fonts/HelveticaNeue.ttc", sz, index=1 if bold else 0)
    except Exception:
        return ImageFont.load_default(sz)


def mask(n):
    ss = 4
    m = Image.new("L", (n * ss, n * ss), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, n * ss - 1, n * ss - 1], radius=int(0.2237 * n * ss), fill=255)
    return m.resize((n, n), Image.LANCZOS)


def at(n):
    return icon if n == 1024 else icon.resize((n, n), Image.LANCZOS)


def put(img, n, xy, masked=True):
    tile = at(n)
    if masked:
        img.paste(tile, xy, mask(n))
    else:
        img.paste(tile, xy)
    return tile


# Busy mid-grey ground: overlapping grey shapes, a few muted colours, thin lines.
bg = Image.new("RGB", (W, H), (128, 128, 128))
d = ImageDraw.Draw(bg)
for _ in range(420):
    g = rnd.randint(88, 172)
    col = (g, g, g) if rnd.random() < 0.8 else tuple(min(255, max(0, g + rnd.randint(-40, 40))) for _ in range(3))
    x, y = rnd.randint(-80, W), rnd.randint(-80, H)
    s = rnd.randint(12, 150)
    k = rnd.random()
    if k < 0.4:
        d.rectangle([x, y, x + s, y + rnd.randint(8, 150)], fill=col)
    elif k < 0.75:
        d.ellipse([x, y, x + s, y + s], fill=col)
    else:
        d.line([x, y, x + rnd.randint(-300, 300), y + rnd.randint(-300, 300)], fill=col, width=rnd.randint(2, 9))

img = bg
d = ImageDraw.Draw(img)
lab, small = font(30, True), font(22)


def label(xy, text, f=lab):
    x, y = xy
    tw = d.textbbox((0, 0), text, font=f)
    d.rectangle([x - 8, y - 5, x + tw[2] + 8, y + tw[3] + 7], fill=(20, 20, 24))
    d.text((x, y), text, font=f, fill=(255, 255, 255))


label((48, 40), "1024 - iOS mask shown for display; the PNG is full-bleed, no alpha")
put(img, 1024, (48, 100))

X = 1140
label((X, 40), "180 (apple-touch-icon)")
put(img, 180, (X, 100))

# Home-screen row at 60px: our icon among other app tiles.
label((X, 330), "60 - home screen row")
y60 = 390
xs = [X + i * 87 for i in range(6)]
for i, x in enumerate(xs):
    if i == 2:
        put(img, 60, (x, y60))
        continue
    c = tuple(rnd.randint(30, 230) for _ in range(3))
    t = Image.new("RGB", (60, 60), c)
    td = ImageDraw.Draw(t)
    fg = tuple(255 - v for v in c)
    if i % 2:
        td.ellipse([14, 14, 46, 46], outline=fg, width=6)
    else:
        td.rectangle([16, 18, 44, 42], fill=fg)
    img.paste(t, (x, y60), mask(60))
    d.text((x + 4, y60 + 66), "App", font=font(15), fill=(255, 255, 255))
d.text((xs[2] + 4, y60 + 66), "Addify", font=font(15), fill=(255, 255, 255))

label((X, 500), "20 - settings / spotlight row")
put(img, 20, (X, 555))
d.text((X + 32, 553), "Addify", font=font(20), fill=(255, 255, 255))
put(img, 20, (X + 150, 555), masked=False)
d.text((X + 182, 553), "(unmasked)", font=font(20), fill=(255, 255, 255))

label((X, 620), "60 at 4x and 20 at 8x (real pixels)")
t60 = at(60).resize((240, 240), Image.NEAREST)
img.paste(t60, (X, 680))
t20 = at(20).resize((160, 160), Image.NEAREST)
img.paste(t20, (X + 270, 680))

img.save(out, "PNG", optimize=True)
print("wrote", out, img.size)

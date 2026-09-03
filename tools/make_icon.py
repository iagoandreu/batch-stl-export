"""Generate the toolbar icon set for the Batch STL Export command.

Three offset faceted plates - the batch - with an export arrow leaving the front
one. Same palette as the Batch STL Export icon. Rendered at 8x and downsampled so it
stays readable at 16 px.
"""
import os
from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "resources", "BatchSTLExport")
os.makedirs(OUT, exist_ok=True)

BACK_FILL = (208, 214, 221, 255)
BACK_EDGE = (140, 150, 161, 255)
FRONT_FILL = (232, 238, 244, 255)
FRONT_EDGE = (94, 106, 120, 255)
ARROW = (44, 122, 168, 255)


def plate(d, x, y, w, h, fill, edge, lw, facets=False):
    """A parallelogram seen in shallow isometric - reads as a part on a bed."""
    sk = h * 0.55
    pts = [(x + sk, y), (x + w, y), (x + w - sk, y + h), (x, y + h)]
    d.polygon(pts, fill=fill, outline=edge, width=lw)
    if facets:
        for f in (0.30, 0.55, 0.78):
            top = (x + sk + (w - sk) * f, y)
            bot = (x + (w - sk) * f, y + h)
            d.line([top, bot], fill=edge, width=max(1, int(lw * 0.7)))
        d.line([(x + sk, y), (x + w - sk, y + h)], fill=edge,
               width=max(1, int(lw * 0.7)))


def render(size):
    S = size * 8
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    lw = max(1, int(S * 0.020))

    w, h = S * 0.60, S * 0.19
    x0, y0 = S * 0.07, S * 0.14
    step = S * 0.115
    for i in (2, 1):                       # back plates first
        plate(d, x0 + step * 0.30 * i, y0 + step * i, w, h,
              BACK_FILL, BACK_EDGE, lw)
    plate(d, x0, y0, w, h, FRONT_FILL, FRONT_EDGE, lw, facets=True)

    # export arrow, leaving the stack to the lower right
    ax, ay = S * 0.72, S * 0.50
    shaft = S * 0.115
    d.rectangle([ax - shaft * 0.42, ay, ax + shaft * 0.42, ay + S * 0.20],
                fill=ARROW)
    head = S * 0.15
    d.polygon([(ax - head, ay + S * 0.19), (ax + head, ay + S * 0.19),
               (ax, ay + S * 0.19 + head * 1.15)], fill=ARROW)
    d.rectangle([ax - head * 1.15, ay + S * 0.38 + head * 0.05,
                 ax + head * 1.15, ay + S * 0.38 + head * 0.05 + lw * 2.4],
                fill=ARROW)

    return img.resize((size, size), Image.LANCZOS)


for n in (16, 32, 64):
    render(n).save(os.path.join(OUT, "%dx%d.png" % (n, n)))
print("wrote 16/32/64 px icons to", OUT)

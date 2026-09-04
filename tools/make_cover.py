"""Render the submission cover image.

    python tools/make_cover.py

1200x630, which is the size every platform crops least badly. The brief it is
written to: a cover has about one second to earn a click, and the one thing
this project has that no equity curve can show is the agent *refusing* things.
So the image is a terminal frame of two real refusals, taken verbatim from
`tools/demo.py`, rather than a chart.

Nothing here is decorative text. Every string on the image is either a fact
about the account or a line the kernel actually emits.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "docs" / "cover.png"
W, H = 1200, 630

# The terminal palette the rest of the project uses: cool near-black, muted
# ANSI. A saturated "trading green" would fight the point being made.
GROUND = (11, 14, 20)
PANEL = (16, 20, 27)
EDGE = (35, 42, 54)
TEXT = (200, 208, 220)
DIM = (107, 118, 134)
RED = (224, 104, 95)
ACCENT = (91, 139, 208)
WHITE = (232, 238, 246)

FONTS = Path("C:/Windows/Fonts")


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def main() -> int:
    img = Image.new("RGB", (W, H), GROUND)
    d = ImageDraw.Draw(img)

    sans_b = font("segoeuib.ttf", 58)
    sans = font("segoeui.ttf", 25)
    mono = font("consola.ttf", 19)
    mono_b = font("consolab.ttf", 19)
    mono_s = font("consola.ttf", 16)

    # --- wordmark and thesis --------------------------------------------------
    d.text((64, 58), "Glassbox", font=sans_b, fill=WHITE)
    d.text(
        (66, 132),
        "An options agent whose AI cannot author a trade.",
        font=sans,
        fill=TEXT,
    )
    d.text(
        (66, 168),
        "It picks one pre-priced candidate, or nothing at all.",
        font=sans,
        fill=DIM,
    )

    # --- the refusals, verbatim from tools/demo.py ----------------------------
    panel = (64, 236, W - 64, 236 + 250)
    d.rounded_rectangle(panel, radius=10, fill=PANEL, outline=EDGE, width=1)

    x, y = 92, 266
    d.text((x, y), "$ python tools/demo.py", font=mono, fill=ACCENT)

    y += 42
    rows = [
        ("REFUSED", "sell 400 naked SPY calls", "02_bounded_max_loss"),
        ("REFUSED", "a hallucinated ticker", "01_symbol_allowlist"),
    ]
    for tag, plan, invariant in rows:
        d.text((x, y), tag, font=mono_b, fill=RED)
        d.text((x + 108, y), plan, font=mono, fill=TEXT)
        d.text((x + 108, y + 26), invariant, font=mono_s, fill=DIM)
        y += 72

    d.text(
        (x, y - 4),
        "4 hostile plans, 4 different invariants, no credentials needed",
        font=mono_s,
        fill=DIM,
    )

    # --- footer ---------------------------------------------------------------
    d.line((64, H - 92, W - 64, H - 92), fill=EDGE, width=1)
    d.text(
        (64, H - 68),
        "Alpaca paper account PA3XT8QFJZAQ",
        font=mono_s,
        fill=DIM,
    )
    repo = "github.com/jacklachan/alpaca"
    right = d.textlength(repo, font=mono_s)
    d.text((W - 64 - right, H - 68), repo, font=mono_s, fill=DIM)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "PNG", optimize=True)
    print(f"wrote {OUT}  ({W}x{H}, {OUT.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

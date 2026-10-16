"""Generate the platform logo: SVG mark and wordmark (light/dark) and PNG sizes.

    python ai_platform/docs/logo/make_logo.py

Change NAME and TAGLINE below to rename the wordmark. The mark is a sealed
registration (the rounded square) with one door (the gap) and a person standing
in it (the dot): nothing gets in except through a human. The candles are the
paper trades.
"""
from pathlib import Path

NAME = "Trading Research Platform"
TAGLINE = "paper trading · pre-registered experiments · measured"
OUT = Path(__file__).parent
INK, PAPER, ACCENT_L, ACCENT_D = "#1D2127", "#F4F5F3", "#B4541C", "#E0834A"
FONT = '"IBM Plex Sans", "Helvetica Neue", Arial, sans-serif'


def mark_svg(fg: str, accent: str) -> str:
    """The mark in a 100x100 space."""
    return (
        f'<path d="M44 14 H72 A14 14 0 0 1 86 28 V72 A14 14 0 0 1 72 86 H28 A14 14 0 0 1 14 72 V28 A14 14 0 0 1 28 14 H30" '
        f'fill="none" stroke="{fg}" stroke-width="6" stroke-linecap="butt"/>'
        f'<circle cx="37" cy="14" r="5" fill="{accent}"/>'
        f'<line x1="36" y1="48" x2="36" y2="74" stroke="{fg}" stroke-width="2.5"/><rect x="32" y="54" width="8" height="16" fill="{fg}"/>'
        f'<line x1="50" y1="40" x2="50" y2="72" stroke="{fg}" stroke-width="2.5"/><rect x="46" y="46" width="8" height="20" fill="{fg}"/>'
        f'<line x1="64" y1="30" x2="64" y2="68" stroke="{fg}" stroke-width="2.5"/><rect x="60" y="36" width="8" height="26" fill="{fg}"/>'
    )


def svg(width, height, body, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img">'
            f'<title>{title}</title>{body}</svg>\n')


def write_svgs():
    # transparent mark, ink and paper versions
    (OUT / "mark.svg").write_text(svg(100, 100, mark_svg(INK, ACCENT_L), NAME))
    (OUT / "mark-on-dark.svg").write_text(svg(100, 100, mark_svg(PAPER, ACCENT_D), NAME))
    # tiles for avatars
    for name, bg, fg, ac in (("mark-tile-light.svg", PAPER, INK, ACCENT_L), ("mark-tile-dark.svg", INK, PAPER, ACCENT_D)):
        (OUT / name).write_text(svg(120, 120, f'<rect width="120" height="120" rx="20" fill="{bg}"/><g transform="translate(10,10)">{mark_svg(fg, ac)}</g>', NAME))
    # horizontal lockup
    for name, fg, ac, muted in (("wordmark-light.svg", INK, ACCENT_L, "#5B6370"), ("wordmark-dark.svg", PAPER, ACCENT_D, "#A2A9B3")):
        body = (f'<g transform="translate(0,0)">{mark_svg(fg, ac)}</g>'
                f'<text x="118" y="52" font-family=\'{FONT}\' font-size="34" font-weight="600" fill="{fg}">{NAME}</text>'
                f'<text x="119" y="76" font-family=\'{FONT}\' font-size="14" fill="{muted}" letter-spacing="0.3">{TAGLINE}</text>')
        (OUT / name).write_text(svg(600, 100, body, NAME))


def draw_mark(d, x0, y0, s, fg, accent, bg):
    """Same geometry with PIL, scaled by s from the 100-unit space."""
    def P(v): return v * s
    w = max(2, round(6 * s))
    d.rounded_rectangle([x0 + P(14), y0 + P(14), x0 + P(86), y0 + P(86)], radius=P(14), outline=fg, width=w)
    # the door: erase the top edge between x=30 and x=44 (square ends), the person stands in it
    d.rectangle([x0 + P(30), y0 + P(14) - w, x0 + P(44) - 1, y0 + P(14) + w], fill=bg)
    d.ellipse([x0 + P(32), y0 + P(9), x0 + P(42), y0 + P(19)], fill=accent)
    wick = max(1, round(2.5 * s))
    for cx, top, bot, by0, by1 in ((36, 48, 74, 54, 70), (50, 40, 72, 46, 66), (64, 30, 68, 36, 62)):
        d.line([x0 + P(cx), y0 + P(top), x0 + P(cx), y0 + P(bot)], fill=fg, width=wick)
        d.rectangle([x0 + P(cx - 4), y0 + P(by0), x0 + P(cx + 4), y0 + P(by1)], fill=fg)


def write_pngs():
    from PIL import Image, ImageDraw, ImageFont
    bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    book = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    # avatar tiles
    for name, bg, fg, ac in (("mark-tile-light-512.png", PAPER, INK, ACCENT_L), ("mark-tile-dark-512.png", INK, PAPER, ACCENT_D)):
        im = Image.new("RGB", (512, 512), bg); d = ImageDraw.Draw(im)
        draw_mark(d, 6, 6, 5.0, fg, ac, bg)
        im.save(OUT / name)
    # social previews (GitHub 1280x640, LinkedIn 1200x627)
    for name, size, bg, fg, ac, muted in (("social-preview-1280x640.png", (1280, 640), PAPER, INK, ACCENT_L, "#5B6370"),
                                          ("social-preview-dark-1280x640.png", (1280, 640), INK, PAPER, ACCENT_D, "#A2A9B3"),
                                          ("linkedin-1200x627.png", (1200, 627), PAPER, INK, ACCENT_L, "#5B6370")):
        im = Image.new("RGB", size, bg); d = ImageDraw.Draw(im)
        s = 2.6
        f1 = ImageFont.truetype(bold, 56); f2 = ImageFont.truetype(book, 24)
        gap = 44
        total = 100 * s + gap + max(d.textlength(NAME, font=f1), d.textlength(TAGLINE, font=f2))
        mx, my = (size[0] - total) / 2, (size[1] - 100 * s) / 2 - 10
        draw_mark(d, mx, my, s, fg, ac, bg)
        tx = mx + 100 * s + gap
        d.text((tx, my + 78), NAME, font=f1, fill=fg)
        d.text((tx + 2, my + 152), TAGLINE, font=f2, fill=muted)
        im.save(OUT / name)


if __name__ == "__main__":
    write_svgs(); write_pngs()
    print(sorted(p.name for p in OUT.iterdir()))

#!/usr/bin/env python3
"""Render the profile README cards into cards/ as self-contained animated SVGs.

    python scripts/render_cards.py            animated cards -> cards/
    STATIC=1 python scripts/render_cards.py   final frame, no animation -> preview/
                                              (open preview/index.html and screenshot)

The cards are shown through <img>, so each one carries everything it needs: an inline <style>
with CSS keyframes, SMIL for motion along a path, and its fonts, subset to the glyphs the card
uses (pyftsubset) and embedded as base64 WOFF2. Contribution and repo data come from
data/github.json, which the GitHub Action refreshes daily (query: data/query.graphql).

Look: davidklingbeil.org ("Cell state"). Light grey paper, ink wires, one gate yellow that is
only ever a fill behind ink, square shapes, circles for pointwise operations. Motion: a gate
opens on a sigmoid, then what passed it is carried along the line.

Needs: pip install fonttools brotli
"""
from __future__ import annotations

import base64
import datetime as dt
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parent.parent
# Fixed font timestamps: the same data must give byte-identical cards, or the Action commits daily.
os.environ["SOURCE_DATE_EPOCH"] = "0"
STATIC = os.environ.get("STATIC") == "1"
OUT = ROOT / ("preview" if STATIC else "cards")
DATA = ROOT / "data" / "github.json"

# --------------------------------------------------------------------------- content

NAME = "David Klingbeil"
ROLE = "AI student building software, infrastructure and models."
EYEBROW = "BSc Artificial Intelligence, JKU Linz"
PLACE = "Linz, Austria"
TAGLINE = "Open to internships and working-student roles."
# Optional portrait: set PORTRAIT=/path/to/photo or drop assets/portrait.(jpg|png|webp).
PORTRAIT = os.environ.get("PORTRAIT")

TILES = [  # file, label, title, small print, filled (the one primary action is a gate)
    ("tile-portfolio", "Website", "Portfolio", "davidklingbeil.org", False),
    ("tile-cv", "Curriculum vitae", "CV", "davidklingbeil.org/cv", False),
    ("tile-email", "Contact", "Email", "dklingbeil777@gmail.com", True),
]

REPOS = [  # description=None takes the one on GitHub
    dict(file="repo-orbit", repo="Orbit", title="Orbit", description=None,
         out="Offline", out_label="runs entirely on your Mac",
         tokens=["Swift", "macOS", "Open source"]),
    dict(file="repo-city-wallet", repo="City-Wallet", title="Lora",
         description="A city wallet that ranks nearby shops’ offers with a model on the phone "
                     "itself, so the profile never leaves the device.",
         out="2nd", out_label="place at Hack-Nation Linz",
         tokens=["React Native", "TypeScript", "On-device ranking"]),
    dict(file="repo-image-classification", repo="Image-Classification-Project",
         title="Image classifier",
         description="A convolutional network that sorts images into 20 classes, scored on a "
                     "test set it never saw.",
         out="73.4%", out_label="on the hidden test set",
         tokens=["Python", "PyTorch", "CNNs"]),
    dict(file="repo-sound-events", repo="MLCP-Project", title="Sound events",
         description="A bidirectional GRU that detects ten classes of urban sound, with a "
                     "threshold tuned per class. Predicting nothing costs 108.9.",
         out="37.7", out_label="final cost, lower is better",
         tokens=["Python", "PyTorch Lightning", "Recurrent nets"]),
]

# Seconds before each card starts, so the page plays top to bottom.
DELAY = {"hero": 0.0, "tiles": 1.0, "activity": 1.9, "repos": 2.9, "graph": 3.9}

# --------------------------------------------------------------------------- tokens

BG, PAPER, INK, MUTED, GATE = "#f1f2f2", "#ffffff", "#0c0d0d", "#585c5e", "#ffd12e"
LINE = "rgba(12,13,13,0.16)"
ON_INK_MUTED = "#a4a8aa"

# Static instances cut from the site's variable fonts: (file, axes).
FACES = {
    "disp": ("archivo-100_900-normal.woff2", {"wght": 800, "wdth": 125}),
    "text": ("archivo-100_900-normal.woff2", {"wght": 400, "wdth": 100}),
    "bold": ("archivo-100_900-normal.woff2", {"wght": 620, "wdth": 100}),
    "tok": ("archivo-100_900-normal.woff2", {"wght": 600, "wdth": 86}),
    "math": ("stix-400_700-italic.woff2", {"wght": 400}),
    "sig": ("stix-sigma-italic.woff2", {"wght": 400}),
}
FALLBACK = {"math": "'Times New Roman',serif", "sig": "'Times New Roman',serif"}

GATE_EASE = "cubic-bezier(.65,0,.35,1)"   # the logistic curve: openings
CARRY_EASE = "cubic-bezier(.16,1,.3,1)"   # expo.out: carries

ANIM_CSS = f"""
.draw{{stroke-dasharray:1;animation:draw .9s {GATE_EASE} both}}
@keyframes draw{{from{{stroke-dashoffset:1}}}}
.gy{{transform-box:fill-box;transform-origin:50% 100%;animation:gy .55s {GATE_EASE} both}}
@keyframes gy{{from{{transform:scaleY(0)}}}}
.gx{{transform-box:fill-box;transform-origin:0 50%;animation:gx .55s {GATE_EASE} both}}
@keyframes gx{{from{{transform:scaleX(0)}}}}
.up{{animation:up .9s {CARRY_EASE} both}}
@keyframes up{{from{{opacity:0;transform:translateY(14px)}}}}
.carry{{animation:carry .8s {CARRY_EASE} both}}
@keyframes carry{{from{{opacity:0;transform:translateX(-28px)}}}}
.rise{{animation:rise 1s {CARRY_EASE} both}}
@keyframes rise{{from{{transform:translateY(120px)}}}}
.ride{{animation:ride .9s {GATE_EASE} both}}
@keyframes ride{{from{{transform:translateX(var(--d));opacity:0}}12%{{opacity:1}}}}
.fd{{animation:fd .5s ease-out both}}
@keyframes fd{{from{{opacity:0}}}}
.still{{display:none}}
@media (prefers-reduced-motion:reduce){{
*{{animation:none!important}}
.smil{{display:none}}
.still{{display:inline}}
}}
"""

# --------------------------------------------------------------------------- fonts

_tmp = Path(tempfile.mkdtemp(prefix="cards-fonts-"))
_instances: dict[str, tuple[Path, dict, dict]] = {}


def instance(face: str):
    """Cut the static instance for a face once; return (ttf path, cmap, advances)."""
    if face not in _instances:
        file, axes = FACES[face]
        font = TTFont(ROOT / "fonts" / file)
        font = instancer.instantiateVariableFont(font, axes)
        font.flavor = None
        path = _tmp / f"{face}.ttf"
        font.save(path)
        _instances[face] = (path, font.getBestCmap(), {g: a for g, (a, _) in font["hmtx"].metrics.items()})
    return _instances[face]


def measure(s: str, face: str, size: float, ls: float = 0.0) -> float:
    """Advance width of s in px (ls = letter-spacing in em). Kerning is ignored."""
    _, cmap, adv = instance(face)
    units = sum(adv.get(cmap.get(ord(ch), ".notdef"), 500) for ch in s)
    return units * size / 1000 + ls * size * len(s)


def fit(s: str, face: str, avail: float, max_size: float, ls: float = 0.0) -> float:
    return round(min(max_size, max_size * avail / measure(s, face, max_size, ls)), 1)


def wrap(s: str, face: str, size: float, width: float) -> list[str]:
    lines, cur = [], ""
    for word in s.split():
        trial = f"{cur} {word}".strip()
        if cur and measure(trial, face, size) > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + [cur] if cur else lines


def font_face(face: str, chars: set[str]) -> str:
    """Subset a face to chars with pyftsubset and return its @font-face rule."""
    src, _, _ = instance(face)
    out = _tmp / f"{face}-subset.woff2"
    exe = shutil.which("pyftsubset")
    cmd = [exe] if exe else [sys.executable, "-m", "fontTools.subset"]
    subprocess.run(
        cmd + [str(src), f"--output-file={out}", "--flavor=woff2", "--no-hinting",
               "--desubroutinize", "--layout-features=kern", "--no-recalc-timestamp",
               "--unicodes=" + ",".join(f"{ord(c):04X}" for c in sorted(chars))],
        check=True, stdout=subprocess.DEVNULL)
    b64 = base64.b64encode(out.read_bytes()).decode()
    return f"@font-face{{font-family:'{face}';src:url(data:font/woff2;base64,{b64}) format('woff2')}}"


# --------------------------------------------------------------------------- card


def f(n: float) -> str:
    return f"{n:.2f}".rstrip("0").rstrip(".")


def anim(cls: str, delay: float, extra: str = "") -> str:
    """class + delay for an animated element. Its attributes are the final frame; the keyframes
    only describe where it comes from, so with no CSS (STATIC, reduced motion) it rests there."""
    if STATIC:
        return ""
    return f' class="{cls}" style="animation-delay:{f(delay)}s{";" + extra if extra else ""}"'


class Card:
    def __init__(self, name: str, w: int, h: int, delay: float, title: str, bg: str = BG):
        self.name, self.w, self.h, self.t0, self.title = name, w, h, delay, title
        self.chars: dict[str, set[str]] = {}
        self.body: list[str] = [f'<rect width="{w}" height="{h}" fill="{bg}"/>']
        self.defs: list[str] = []

    def at(self, t: float) -> float:
        return self.t0 + t

    def add(self, s: str):
        self.body.append(s)

    def text(self, x, y, s, face="text", size=16, fill=INK, anchor="start", ls=0.0, a="") -> float:
        self.chars.setdefault(face, set()).update(s)
        attrs = f' text-anchor="{anchor}"' if anchor != "start" else ""
        attrs += f' letter-spacing="{f(ls * size)}"' if ls else ""
        self.add(f'<text x="{f(x)}" y="{f(y)}" font-family="\'{face}\',{FALLBACK.get(face, "Arial,sans-serif")}" '
                 f'font-size="{f(size)}" fill="{fill}"{attrs}{a}>{html.escape(s)}</text>')
        return measure(s, face, size, ls)

    def wire(self, x1, y1, x2, y2, width=2, stroke=INK, a=""):
        self.add(f'<path d="M{f(x1)} {f(y1)}L{f(x2)} {f(y2)}" pathLength="1" fill="none" '
                 f'stroke="{stroke}" stroke-width="{width}"{a}/>')

    def pulse(self, y: float, begin: float, fill=GATE, stroke=INK, dur=7):
        """A dot riding the state line (SMIL). Hidden in STATIC and under reduced motion."""
        if STATIC:
            return
        self.add(f'<circle class="smil" cx="-12" cy="{f(y)}" r="6" fill="{fill}" stroke="{stroke}" stroke-width="2">'
                 f'<animateMotion path="M0 0H{self.w + 24}" begin="{f(begin)}s" dur="{dur}s" '
                 f'repeatCount="indefinite"/></circle>')

    def save(self):
        fonts = "".join(font_face(face, chars) for face, chars in sorted(self.chars.items()))
        css = fonts + ("" if STATIC else ANIM_CSS.replace("\n", ""))
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" '
               f'width="{self.w}" height="{self.h}" role="img" aria-label="{html.escape(self.title)}">'
               f'<title>{html.escape(self.title)}</title><style>{css}</style>'
               f'<defs>{"".join(self.defs)}</defs>{"".join(self.body)}</svg>\n')
        (OUT / f"{self.name}.svg").write_text(svg, encoding="utf-8")
        print(f"{self.name}.svg  {len(svg) / 1024:.1f} KB")


# --------------------------------------------------------------------------- data


def load():
    user = json.loads(DATA.read_text())["data"]["user"]
    cal = user["contributionsCollection"]["contributionCalendar"]
    weeks = [[(dt.date.fromisoformat(d["date"]), d["contributionCount"], d["contributionLevel"])
              for d in w["contributionDays"]] for w in cal["weeks"]]
    repos = {r["name"]: r for r in user["repositories"]["nodes"]}
    return user["login"], cal["totalContributions"], weeks, repos


def stats(weeks):
    days = [d for w in weeks for d in w]
    counts = [c for _, c, _ in days]
    # Today may still be empty; a streak that reached yesterday is still running.
    tail = counts[:-1] if counts and counts[-1] == 0 else counts
    current = next((i for i, c in enumerate(reversed(tail)) if c == 0), len(tail))
    longest = run = 0
    for c in counts:
        run = run + 1 if c else 0
        longest = max(longest, run)
    busiest = max(days, key=lambda d: d[1])
    months: dict[tuple[int, int], int] = {}
    for date, c, _ in days:
        months[(date.year, date.month)] = months.get((date.year, date.month), 0) + c
    return dict(current=current, longest=longest, active=sum(1 for c in counts if c), span=len(days),
                busiest=busiest, months=list(months.items())[-12:])


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()

# --------------------------------------------------------------------------- cards


def portrait_uri() -> str | None:
    path = Path(PORTRAIT) if PORTRAIT else next(
        (p for ext in ("jpg", "jpeg", "png", "webp") if (p := ROOT / "assets" / f"portrait.{ext}").exists()), None)
    if not path or not path.exists():
        return None
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}[path.suffix.lower().lstrip(".")]
    return f"data:image/{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def hero():
    W, H, M = 880, 320, 40
    c = Card("hero", W, H, DELAY["hero"], f"{NAME}. {ROLE}")
    photo = portrait_uri()
    P = 150  # portrait edge
    avail = W - 2 * M - (P + 28 if photo else 0)
    line_y = 216

    c.text(M, 50, EYEBROW, "text", 16, MUTED, a=anim("fd", c.at(0.5)))
    if not photo:
        c.text(W - M, 50, PLACE, "text", 16, MUTED, anchor="end", a=anim("fd", c.at(0.6)))

    size = fit(NAME, "disp", avail, 92, -0.045)
    base = line_y - 36
    c.defs.append(f'<clipPath id="n"><rect x="0" y="{f(base - size)}" width="{W}" height="{f(size * 1.26)}"/></clipPath>')
    c.add('<g clip-path="url(#n)">')
    c.text(M, base, NAME, "disp", size, ls=-0.045, a=anim("rise", c.at(0.25)))
    c.add("</g>")

    if photo:
        x, y = W - M - P, line_y - 30 - P
        c.add(f'<g{anim("fd", c.at(0.7))}><image href="{photo}" x="{x}" y="{y}" width="{P}" height="{P}" '
              f'preserveAspectRatio="xMidYMid slice"/><rect x="{x}" y="{y}" width="{P}" height="{P}" '
              f'fill="none" stroke="{INK}" stroke-width="2"/></g>')

    # The cell: an input gate (σ) below the line writes into the state at the + node.
    gx, gs = W - M - 44, 44
    gy_ = line_y + 34
    cx = gx + gs / 2
    c.wire(cx, gy_, cx, line_y, 2, a=anim("draw", c.at(1.35)))
    c.wire(0, line_y, W, line_y, 4, a=anim("draw", c.at(0)))
    c.pulse(line_y, c.at(2.4))
    c.add(f'<rect x="{gx}" y="{gy_}" width="{gs}" height="{gs}" fill="{PAPER}"/>')
    c.add(f'<rect x="{gx}" y="{gy_}" width="{gs}" height="{gs}" fill="{GATE}"{anim("gy", c.at(0.9))}/>')
    c.add(f'<rect x="{gx}" y="{gy_}" width="{gs}" height="{gs}" fill="none" stroke="{INK}" stroke-width="2"/>')
    c.text(cx, gy_ + 31, "σ", "sig", 28, anchor="middle")
    c.add(f'<g{anim("fd", c.at(1.7))}><circle cx="{f(cx)}" cy="{line_y}" r="15" fill="{PAPER}" stroke="{INK}" stroke-width="2"/>'
          f'<path d="M{f(cx - 7)} {line_y}h14M{f(cx)} {line_y - 7}v14" stroke="{INK}" stroke-width="2"/></g>')

    c.text(M, line_y + 50, ROLE, "text", fit(ROLE, "text", gx - M - 24, 23), a=anim("up", c.at(1.0)))
    c.text(M, line_y + 80, TAGLINE, "text", 16, MUTED,
           a=anim("up", c.at(1.15)))
    c.save()


def tiles():
    W, H, M = 400, 190, 28
    for i, (file, label, title, small, filled) in enumerate(TILES):
        c = Card(file, W, H, DELAY["tiles"] + 0.15 * i, f"{title}: {small}")
        if filled:
            c.add(f'<rect width="{W}" height="{H}" fill="{GATE}"{anim("gy", c.at(0.1))}/>')
        c.add(f'<rect x="1" y="1" width="{W - 2}" height="{H - 2}" fill="none" stroke="{INK if filled else LINE}" stroke-width="2"/>')
        c.text(M, 44, label, "text", 20, INK if filled else MUTED, a=anim("fd", c.at(0.2)))
        c.text(M, 108, title, "disp", 52, ls=-0.03, a=anim("up", c.at(0.25)))
        # A link that goes somewhere: a wire draws under it and the arrow rides its tip.
        y, x2 = 130, W - M
        c.wire(M, y, x2 - 1, y, 3, a=anim("draw", c.at(0.45)))
        c.add(f'<path d="M{x2 - 11} {y - 10}L{x2 - 1} {y}L{x2 - 11} {y + 10}" fill="none" stroke="{INK}" '
              f'stroke-width="3"{anim("ride", c.at(0.45), f"--d:-{x2 - M - 12}px")}/>')
        c.text(M, 164, small, "text", 19, INK if filled else MUTED, a=anim("fd", c.at(0.9)))
        c.save()


def activity(total, weeks):
    W, H, M = 880, 380, 40
    s = stats(weeks)
    c = Card("activity", W, H, DELAY["activity"],
             f"Contributions per month. {plural(total, 'contribution')} in the last year, "
             f"current streak {plural(s['current'], 'day')}, {s['active']} active days, "
             f"busiest day {s['busiest'][1]}.")
    c.text(M, 54, "Contributions per month", "bold", 20, a=anim("fd", c.at(0)))
    x0, x1, top, base = M, 560, 104, 312
    c.text(x1, 54, f"{total:,} in the last year", "text", 16, MUTED, anchor="end", a=anim("fd", c.at(0.1)))

    months = s["months"]
    peak = max(v for _, v in months) or 1
    step = (x1 - x0) / len(months)
    bw = 30
    for i, ((year, month), v) in enumerate(months):
        x = x0 + i * step + (step - bw) / 2
        h = round((base - top) * v / peak, 1)
        t = c.at(0.5 + 0.05 * i)
        if v:
            # One series, so ink; the busiest month is the open gate (a fill, ink still outlines it).
            hot = v == peak
            c.add(f'<rect x="{f(x + 1)}" y="{f(base - h)}" width="{bw - 2}" height="{f(h)}" fill="{GATE if hot else INK}" '
                  f'stroke="{INK}" stroke-width="2"{anim("gy", t)}/>')
        c.text(x + bw / 2, base - h - 9, str(v), "bold", 14, INK if v else MUTED, anchor="middle", a=anim("fd", t + 0.45))
        c.text(x + bw / 2, base + 24, MONTHS[month - 1], "text", 14, MUTED, anchor="middle", a=anim("fd", c.at(0.3)))
        if month == 1 or i == 0:
            c.text(x + bw / 2, base + 43, str(year), "text", 12, MUTED, anchor="middle", a=anim("fd", c.at(0.3)))
    c.wire(x0, base, x1, base, 2, a=anim("draw", c.at(0.15)))

    day, n, _ = s["busiest"]
    rows = [
        # With no streak running today, lead with the longest one of the year.
        ("Current streak", plural(s["current"], "day"), f"longest {plural(s['longest'], 'day')}", False) if s["current"]
        else ("Longest streak", plural(s["longest"], "day"), "none running today", False),
        ("Active days", str(s["active"]), f"of the last {s['span']}", False),
        ("Busiest day", str(n), f"{plural(n, 'contribution')[len(str(n)) + 1:]} on {day.day} {MONTHS[day.month - 1]} {day.year}", True),
    ]
    sx = 612
    for i, (label, value, sub, hot) in enumerate(rows):
        y = 44 + i * 104
        t = c.at(1.2 + 0.18 * i)
        c.wire(sx, y, W - M, y, 1, LINE, a=anim("draw", t - 0.2))
        c.text(sx, y + 28, label, "text", 15, MUTED, a=anim("fd", t))
        if hot:
            w = measure(value, "disp", 38, -0.03)
            c.add(f'<rect x="{sx - 6}" y="{y + 40}" width="{f(w + 14)}" height="40" fill="{GATE}"{anim("gx", t + 0.1)}/>')
        c.text(sx, y + 72, value, "disp", 38, ls=-0.03, a=anim("up", t + 0.05))
        c.text(sx, y + 94, sub, "text", 14, MUTED, a=anim("fd", t + 0.2))
    c.save()


def repo_cards(login, repos):
    W, H, M = 600, 300, 30
    for i, r in enumerate(REPOS):
        gh = repos.get(r["repo"], {})
        desc = r["description"] or gh.get("description") or ""
        c = Card(r["file"], W, H, DELAY["repos"] + 0.15 * i, f"{r['title']}: {desc}")
        c.add(f'<rect x="1" y="1" width="{W - 2}" height="{H - 2}" fill="none" stroke="{LINE}" stroke-width="2"/>')
        c.text(M, 44, f"{login}/{r['repo']}", "text", 16, MUTED, a=anim("fd", c.at(0.1)))

        # h_t, what came out of the step: a gate that opens behind the number.
        osize = 28
        ow = measure(r["out"], "disp", osize, -0.03) + 24
        ox, oy = W - M - ow, 60
        c.add(f'<rect x="{f(ox)}" y="{oy}" width="{f(ow)}" height="44" fill="{PAPER}"/>')
        c.add(f'<rect x="{f(ox)}" y="{oy}" width="{f(ow)}" height="44" fill="{GATE}"{anim("gy", c.at(0.6))}/>')
        c.add(f'<rect x="{f(ox)}" y="{oy}" width="{f(ow)}" height="44" fill="none" stroke="{INK}" stroke-width="2"/>')
        c.text(ox + 12, oy + 32, r["out"], "disp", osize, ls=-0.03, a=anim("fd", c.at(0.95)))
        c.text(W - M, oy + 66, r["out_label"], "text", 15, MUTED, anchor="end", a=anim("fd", c.at(1.05)))

        tsize = fit(r["title"], "disp", ox - M - 20, 40, -0.03)
        c.text(M, 96, r["title"], "disp", tsize, ls=-0.03, a=anim("up", c.at(0.2)))
        for j, line in enumerate(wrap(desc, "text", 19, W - 2 * M)[:3]):
            c.text(M, 158 + 26 * j, line, "text", 19, a=anim("up", c.at(0.4 + 0.07 * j)))

        line_y = 236
        c.wire(0, line_y, W, line_y, 3, a=anim("draw", c.at(0.3)))
        x = M
        for j, tok in enumerate(r["tokens"]):  # skills written into the state, carried along it
            tw = measure(tok, "tok", 16) + 20
            c.add(f'<g{anim("carry", c.at(1.0 + 0.08 * j))}><rect x="{f(x)}" y="{line_y + 16}" width="{f(tw)}" height="30" '
                  f'fill="{PAPER}" stroke="{INK}" stroke-width="1.5"/>')
            c.text(x + 10, line_y + 37, tok, "tok", 16)
            c.add("</g>")
            x += tw + 8
        meta = [m for m in ((gh.get("primaryLanguage") or {}).get("name"),
                            plural(gh["stargazerCount"], "star") if gh.get("stargazerCount") else None) if m]
        c.text(W - M, line_y + 37, " · ".join(meta), "text", 15, MUTED, anchor="end", a=anim("fd", c.at(1.3)))
        c.save()


def graph(total, weeks):
    W, H = 880, 250
    c = Card("graph", W, H, DELAY["graph"], f"Contribution graph: {plural(total, 'contribution')} in the last year.", bg=INK)
    step, cell = 14.6, 11.6
    x0 = W - 40 - len(weeks) * step + (step - cell)
    y0 = 84
    fills = {"NONE": "rgba(255,255,255,0.09)", "FIRST_QUARTILE": "rgba(255,209,46,0.32)",
             "SECOND_QUARTILE": "rgba(255,209,46,0.56)", "THIRD_QUARTILE": "rgba(255,209,46,0.78)",
             "FOURTH_QUARTILE": GATE}  # one hue, light to full: magnitude

    c.text(40, 48, f"{total:,} contributions in the last year", "bold", 20, PAPER, a=anim("fd", c.at(0)))
    lx = W - 40
    lx -= c.text(lx, 47, "More", "text", 13, ON_INK_MUTED, anchor="end", a=anim("fd", c.at(0.2))) + 8
    for fill in reversed(list(fills.values())):
        lx -= cell
        c.add(f'<rect x="{f(lx)}" y="36.5" width="{cell}" height="{cell}" fill="{fill}"{anim("fd", c.at(0.2))}/>')
        lx -= 3
    c.text(lx - 5, 47, "Less", "text", 13, ON_INK_MUTED, anchor="end", a=anim("fd", c.at(0.2)))

    for row, name in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        c.text(40, y0 + row * step + cell - 1.5, name, "text", 12, ON_INK_MUTED, a=anim("fd", c.at(0.2)))
    last_label = -9
    for i, week in enumerate(weeks):
        x = x0 + i * step
        first = week[0][0]
        if i and first.month != weeks[i - 1][0][0].month and i - last_label > 2 and i < len(weeks) - 1:
            c.text(x, y0 - 9, MONTHS[first.month - 1], "text", 12, ON_INK_MUTED, a=anim("fd", c.at(0.2)))
            last_label = i
        c.add(f'<g{anim("fd", c.at(0.3 + 0.022 * i))}>')
        for date, _, level in week:
            row = (date.weekday() + 1) % 7  # weeks start on Sunday
            c.add(f'<rect x="{f(x)}" y="{f(y0 + row * step)}" width="{cell}" height="{cell}" fill="{fills[level]}"/>')
        c.add("</g>")
    # On the curtain the state line is gate yellow.
    line_y = 216
    c.wire(0, line_y, W, line_y, 3, GATE, a=anim("draw", c.at(0.3)))
    c.pulse(line_y, c.at(1.8), fill=INK, stroke=GATE)
    c.save()


def preview():
    """A page that stacks the cards the way the README does, for a screenshot."""
    row = lambda files, width: " ".join(f'<img src="{x}.svg" width="{width}">' for x in files)
    (OUT / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Cards preview</title>"
        "<style>body{margin:0;background:#fff}main{width:846px;margin:32px auto}img{vertical-align:top;margin-bottom:8px}</style>"
        f'<main><img src="hero.svg" width="100%">{row([t[0] for t in TILES], "32.5%")}'
        f'<img src="activity.svg" width="100%">{row([r["file"] for r in REPOS], "49.5%")}'
        '<img src="graph.svg" width="100%"></main>\n', encoding="utf-8")


def main():
    OUT.mkdir(exist_ok=True)
    login, total, weeks, repos = load()
    hero()
    tiles()
    activity(total, weeks)
    repo_cards(login, repos)
    graph(total, weeks)
    if STATIC:
        preview()
        print(f"final frames in {OUT}/, open {OUT / 'index.html'}")
    shutil.rmtree(_tmp, ignore_errors=True)


if __name__ == "__main__":
    main()

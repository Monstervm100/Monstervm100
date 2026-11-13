#!/usr/bin/env python3
"""Draw every graphic on the profile README from the GitHub GraphQL API.

Standard library only -- deliberately. The daily Action has no install step,
so it starts and finishes in seconds. The one non-stdlib thing we need, a
subset of JetBrains Mono, was baked into `fonts_inline.py` ahead of time by
`make_fonts.py`.

    GITHUB_TOKEN=...  GH_LOGIN=Monstervm100  python scripts/generate_stats.py
    python scripts/generate_stats.py --demo      # synthetic data, no network

Environment:
    GITHUB_TOKEN  required (the Action's built-in token is enough)
    GH_LOGIN      GitHub username            (default: Monstervm100)
    OUT_DIR       where the .svg files land  (default: current directory)
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import random
import sys
import urllib.error
import urllib.request
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from fonts_inline import FONTS  # noqa: E402

API = "https://api.github.com/graphql"
LOGIN = os.environ.get("GH_LOGIN", "Monstervm100")
OUT_DIR = pathlib.Path(os.environ.get("OUT_DIR", "."))

# ---------------------------------------------------------------------------
# Canvas
# ---------------------------------------------------------------------------

W = 620          # every graphic is this wide, so they stack flush in the README
INSET = 2        # breathing room so strokes at the edge are not half-clipped

LIGHT = {"e": "#24292f", "d": "#57606a", "m": "#8c959f", "r": "#d0d7de"}
DARK = {"e": "#f0f6fc", "d": "#c9d1d9", "m": "#8b949e", "r": "#30363d"}

FONT_STACK = 'JBM,ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace'
ADVANCE = 0.600  # JetBrains Mono advance width, in em. Pins our text measuring.

# The ramp the year chart is drawn with, densest last.
RAMP = " :+#@"
RAMP_CUTS = (0, 2, 5, 9)  # 0 -> " ", 1 -> ":", 2-4 -> "+", 5-8 -> "#", 9+ -> "@"


def text_width(s: str, size: float) -> float:
    return len(s) * size * ADVANCE


def esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# SVG scaffolding
# ---------------------------------------------------------------------------

def face(weight: int) -> str:
    return (
        "@font-face{font-family:JBM;font-style:normal;font-weight:%d;"
        "font-display:block;src:url(data:font/woff2;base64,%s) format('woff2')}"
        % (weight, FONTS[weight])
    )


def head(height: int, weights=(400, 600)) -> str:
    """Open an SVG, with the fonts and the two-theme palette inlined.

    GitHub strips <script> and any external stylesheet from a README, and an
    SVG loaded through <img> cannot fetch subresources of its own -- so the
    font has to be base64 here, and the dark theme has to be a media query
    living inside this file. Both survive GitHub's sanitiser.
    """
    faces = "".join(face(w) for w in weights)
    light = "".join(".%s{fill:%s}" % (k, v) for k, v in LIGHT.items() if k != "r")
    dark = "".join(".%s{fill:%s}" % (k, v) for k, v in DARK.items() if k != "r")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}" '
        f'viewBox="0 0 {W} {height}" fill="none" role="img">'
        f"<style>{faces}"
        f"text{{font-family:{FONT_STACK};white-space:pre}}"
        f"{light}.r{{stroke:{LIGHT['r']}}}.bar{{fill:{LIGHT['d']}}}"
        f".ln{{stroke:{LIGHT['d']}}}"
        f"@media(prefers-color-scheme:dark){{{dark}"
        f".r{{stroke:{DARK['r']}}}.bar{{fill:{DARK['d']}}}"
        f".ln{{stroke:{DARK['d']}}}}}"
        f"</style>"
    )


TAIL = "</svg>"


def txt(x, y, s, cls="d", size=12, weight=400, anchor="start") -> str:
    a = f' text-anchor="{anchor}"' if anchor != "start" else ""
    w = f' font-weight="{weight}"' if weight != 400 else ""
    return (
        f'<text x="{x:g}" y="{y:g}" class="{cls}" font-size="{size:g}"{w}{a}'
        f' xml:space="preserve">{esc(s)}</text>'
    )


def fade(begin: float, dur: float = 0.45) -> str:
    return (
        f'<animate attributeName="opacity" from="0" to="1" '
        f'begin="{begin:g}s" dur="{dur:g}s" fill="freeze"/>'
    )


def faded(begin: float, body: str, dur: float = 0.45) -> str:
    """Wrap `body` so it fades up once and stays."""
    return f'<g opacity="0">{fade(begin, dur)}{body}</g>'


_clip_n = 0


def wipe(body: str, x: float, w: float, begin: float, dur: float,
         cursor_top: float = 0, cursor_h: float = 0) -> str:
    """Reveal `body` left-to-right behind a travelling clip edge.

    A 2px bar rides that edge and switches off when it lands -- a cursor
    finishing a line. SMIL, because CSS animation does not survive GitHub.
    """
    global _clip_n
    _clip_n += 1
    cid = f"w{_clip_n}"
    cur = ""
    if cursor_h:
        cur = (
            f'<rect x="{x:g}" y="{cursor_top:g}" width="2" height="{cursor_h:g}" '
            f'class="bar" opacity="0">'
            f'<animate attributeName="x" from="{x:g}" to="{x + w:g}" '
            f'begin="{begin:g}s" dur="{dur:g}s" fill="freeze"/>'
            # One animation rather than two <set>s: renderers disagree about
            # which of two overlapping <set>s wins, and a cursor left switched
            # on at the end of the line is the failure you would notice.
            f'<animate attributeName="opacity" values="0.55;0.55;0" '
            f'keyTimes="0;0.93;1" begin="{begin:g}s" dur="{dur:g}s" '
            f'fill="freeze"/>'
            f"</rect>"
        )
    return (
        f'<defs><clipPath id="{cid}"><rect x="{x:g}" y="0" width="0" height="400">'
        f'<animate attributeName="width" from="0" to="{w:g}" '
        f'begin="{begin:g}s" dur="{dur:g}s" fill="freeze"/>'
        f"</rect></clipPath></defs>"
        f'<g clip-path="url(#{cid})">{body}</g>{cur}'
    )


def write(name: str, body: str) -> None:
    """Write only when the bytes actually differ.

    Together with the `git status` gate in the workflow, this is what keeps
    quiet days out of the commit history.
    """
    path = OUT_DIR / name
    new = body.encode("utf-8")
    if path.exists() and path.read_bytes() == new:
        print(f"  {name:<14} unchanged")
        return
    path.write_bytes(new)
    print(f"  {name:<14} {len(new):,}B")


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

CONTRIB_QUERY = """
query($login:String!,$from:DateTime!,$to:DateTime!){
  user(login:$login){
    contributionsCollection(from:$from,to:$to){
      contributionCalendar{
        totalContributions
        weeks{ contributionDays{ contributionCount date weekday } }
      }
    }
  }
}"""

REPO_QUERY = """
query($login:String!,$after:String){
  user(login:$login){
    repositories(first:100, after:$after, ownerAffiliations:OWNER,
                 isFork:false, privacy:PUBLIC){
      pageInfo{ hasNextPage endCursor }
      nodes{
        primaryLanguage{ name }
        languages(first:12, orderBy:{field:SIZE,direction:DESC}){
          edges{ size node{ name } }
        }
      }
    }
  }
}"""


def gql(query: str, variables: dict, token: str) -> dict:
    payload = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        API,
        data=payload,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": f"{LOGIN}-profile-stats",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"GitHub API returned HTTP {e.code}: {e.read()[:400].decode()}")
    if body.get("errors"):
        raise SystemExit("GraphQL errors: " + json.dumps(body["errors"])[:500])
    if not body.get("data", {}).get("user"):
        raise SystemExit(f"No such user, or the token cannot see it: {LOGIN}")
    return body["data"]["user"]


def window(today: dt.date) -> tuple[str, str]:
    """A whole number of UTC days, so the numbers do not drift with run time."""
    start = today - dt.timedelta(days=364)
    return f"{start:%Y-%m-%d}T00:00:00Z", f"{today:%Y-%m-%d}T23:59:59Z"


def collect(token: str, today: dt.date) -> dict:
    frm, to = window(today)
    user = gql(CONTRIB_QUERY, {"login": LOGIN, "from": frm, "to": to}, token)
    cal = user["contributionsCollection"]["contributionCalendar"]

    by_bytes: Counter = Counter()
    by_repos: Counter = Counter()
    after = None
    while True:
        page = gql(REPO_QUERY, {"login": LOGIN, "after": after}, token)["repositories"]
        for repo in page["nodes"]:
            if repo["primaryLanguage"]:
                by_repos[repo["primaryLanguage"]["name"]] += 1
            for edge in repo["languages"]["edges"]:
                by_bytes[edge["node"]["name"]] += edge["size"]
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]

    return {"calendar": cal, "by_bytes": by_bytes, "by_repos": by_repos}


def demo(today: dt.date) -> dict:
    """Synthetic data shaped exactly like the API's, for offline previews."""
    rng = random.Random(7)
    weeks = []
    day = today - dt.timedelta(days=364)
    day -= dt.timedelta(days=day.isoweekday() % 7)  # rewind to a Sunday
    while day <= today:
        days = []
        for _ in range(7):
            if day > today:
                break
            n = 0 if rng.random() < 0.24 else int(rng.triangular(1, 18, 4))
            days.append({"contributionCount": n, "date": f"{day:%Y-%m-%d}",
                         "weekday": day.isoweekday() % 7})
            day += dt.timedelta(days=1)
        weeks.append({"contributionDays": days})
    total = sum(d["contributionCount"] for w in weeks for d in w["contributionDays"])
    return {
        "calendar": {"totalContributions": total, "weeks": weeks},
        "by_bytes": Counter({"JavaScript": 412_300, "Python": 288_100, "HTML": 141_900,
                             "CSS": 96_400, "TypeScript": 54_800, "Shell": 9_100}),
        "by_repos": Counter({"JavaScript": 9, "Python": 6, "HTML": 4,
                             "TypeScript": 2, "CSS": 2}),
    }


# ---------------------------------------------------------------------------
# Derived numbers
# ---------------------------------------------------------------------------

def flatten(cal: dict) -> list:
    out = []
    for week in cal["weeks"]:
        for d in week["contributionDays"]:
            out.append((dt.date.fromisoformat(d["date"]), d["contributionCount"]))
    out.sort()
    return out


def streaks(days: list, today: dt.date) -> dict:
    best = run = 0
    best_end = None
    for date, n in days:
        if n:
            run += 1
            if run > best:
                best, best_end = run, date
        else:
            run = 0

    # Today is still in progress. A zero there should not break a live streak.
    tail = days[:]
    if tail and tail[-1][0] == today and tail[-1][1] == 0:
        tail.pop()
    current, end = 0, None
    for date, n in reversed(tail):
        if not n:
            break
        current += 1
        end = end or date
    start = end - dt.timedelta(days=current - 1) if current else None
    best_start = best_end - dt.timedelta(days=best - 1) if best else None
    return {"current": current, "current_span": (start, end),
            "longest": best, "longest_span": (best_start, best_end)}


def day_label(d: dt.date) -> str:
    return f"{d:%b %d}".replace(" 0", " ")


def span_label(span) -> str:
    a, b = span
    if not a:
        return "none yet"
    if a == b:
        return day_label(a)
    return f"{day_label(a)} – {day_label(b)}"


def top(counter: Counter, n: int = 5) -> list:
    # Sort by value, then name, so ties do not shuffle between runs.
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


# ---------------------------------------------------------------------------
# The graphics
# ---------------------------------------------------------------------------

def draw_stats(total: int, active: int, best_week: int, weekly: list) -> str:
    H = 150
    s = [head(H)]

    s.append(faded(0.10, txt(INSET, 62, f"{total:,}", "e", 48, 600)))
    s.append(faded(0.22, txt(INSET, 84, "contributions in the last year", "m", 11)))

    for i, (val, lab) in enumerate([(f"{active}", "active days"),
                                    (f"{best_week}", "best week")]):
        x = W - INSET - (150 if i == 0 else 0)
        s.append(faded(0.30 + 0.12 * i,
                       txt(x, 50, val, "e", 20, 600, "end")
                       + txt(x, 66, lab, "m", 10, 400, "end")))

    # Sparkline: one point per week, with the area under it filled.
    x0, x1 = INSET, W - INSET
    y0, y1 = 98, 138
    peak = max(weekly) or 1
    step = (x1 - x0) / max(len(weekly) - 1, 1)
    pts = [(x0 + i * step, y1 - (v / peak) * (y1 - y0)) for i, v in enumerate(weekly)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    spark = (
        f'<polygon points="{x0},{y1} {line} {x1},{y1}" class="bar" opacity=".13"/>'
        f'<polyline points="{line}" fill="none" class="ln" stroke-width="1.4" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
    )
    s.append(wipe(spark, x0, x1 - x0, 0.50, 1.30, cursor_top=y0, cursor_h=y1 - y0))
    s.append(faded(1.80, f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" '
                         f'r="2.6" class="bar"/>', 0.35))
    s.append(TAIL)
    return "".join(s)


def draw_streak(st: dict) -> str:
    H = 98
    s = [head(H)]
    mid = W / 2
    s.append(faded(0.20, f'<line x1="{mid:g}" y1="18" x2="{mid:g}" y2="80" '
                         f'class="r" stroke-width="1"/>'))
    cells = [("current streak", st["current"], span_label(st["current_span"]), INSET),
             ("longest streak", st["longest"], span_label(st["longest_span"]), mid + 24)]
    for i, (label, n, span, x) in enumerate(cells):
        unit = "day" if n == 1 else "days"
        s.append(faded(0.12 + 0.14 * i,
                       txt(x, 30, label, "m", 10)
                       + txt(x, 62, f"{n}", "e", 30, 600)
                       + txt(x + text_width(str(n), 30) + 7, 62, unit, "m", 11)
                       + txt(x, 80, span, "d", 10)))
    s.append(TAIL)
    return "".join(s)


def draw_langs(by_bytes: Counter, by_repos: Counter) -> str:
    H = 162
    s = [head(H)]
    col_w, gap = 276, 44
    total_bytes = sum(by_bytes.values()) or 1

    groups = [
        ("by bytes", top(by_bytes), lambda v: f"{v / total_bytes * 100:.1f}%", 0.34),
        ("by repos", top(by_repos), lambda v: str(v), 0.46),
    ]
    for g, (title, rows, fmt, begin) in enumerate(groups):
        cx = INSET + g * (col_w + gap)
        s.append(faded(0.18 + 0.10 * g, txt(cx, 16, title, "m", 10)))
        if not rows:
            s.append(faded(begin, txt(cx, 44, "nothing public yet", "m", 11)))
            continue
        peak = max(v for _, v in rows)
        bar_x = cx + 92
        bar_max = col_w - 92 - 50
        bars = []
        for r, (name, val) in enumerate(rows):
            y = 38 + r * 22
            bars.append(
                f'<rect x="{bar_x:g}" y="{y - 9:g}" '
                f'width="{max((val / peak) * bar_max, 2):.1f}" height="9" '
                f'rx="2" class="bar" opacity=".55"/>'
            )
            s.append(faded(
                0.22 + 0.10 * g + 0.05 * r,
                txt(cx, y, name.lower()[:11], "d", 11)
                + txt(cx + col_w, y, fmt(val), "m", 10, 400, "end"),
            ))
        s.append(wipe("".join(bars), bar_x, bar_max, begin, 0.95))
    s.append(TAIL)
    return "".join(s)


def draw_year(cal: dict) -> str:
    """The contribution calendar, drawn in characters instead of squares."""
    H = 150
    s = [head(H)]
    size = 8.8
    cw = size * ADVANCE
    gutter = INSET + 26
    line_h = 13
    top_y = 34

    weeks = cal["weeks"]
    grid = [[None] * len(weeks) for _ in range(7)]
    for w, week in enumerate(weeks):
        for d in week["contributionDays"]:
            grid[d["weekday"]][w] = d["contributionCount"]

    def glyph(n):
        if n is None:
            return " "
        for i, cut in enumerate(RAMP_CUTS):
            if n < cut:
                return RAMP[i]
        return RAMP[-1]

    # Month labels, placed on the week the month turns over.
    labels, seen = [], None
    for w, week in enumerate(weeks):
        d0 = dt.date.fromisoformat(week["contributionDays"][0]["date"])
        if d0.month != seen:
            if seen is not None and w < len(weeks) - 2:
                labels.append(txt(gutter + w * 2 * cw, 20, f"{d0:%b}".lower(), "m", 8.5))
            seen = d0.month
    s.append(faded(0.16, "".join(labels)))

    row_names = {1: "mon", 3: "wed", 5: "fri"}
    rows = []
    for r in range(7):
        y = top_y + r * line_h
        if r in row_names:
            s.append(faded(0.20, txt(INSET, y, row_names[r], "m", 8.5)))
        line = "".join(glyph(v) + " " for v in grid[r])
        rows.append(wipe(txt(gutter, y, line, "d", size),
                         gutter, len(weeks) * 2 * cw, 0.30 + 0.07 * r, 0.40))
    s.append("".join(rows))

    # A legend, so density is never the only thing carrying the meaning.
    ly = top_y + 7 * line_h + 16
    ramp = " ".join(RAMP[1:])
    s.append(faded(1.30,
                   txt(gutter, ly, "less", "m", 9)
                   + txt(gutter + 34, ly, ramp, "d", 9)
                   + txt(gutter + 42 + text_width(ramp, 9), ly, "more", "m", 9)))
    s.append(TAIL)
    return "".join(s)


def draw_heading(word: str) -> str:
    """A section heading drawn as type plus a rule, not a markdown '##'."""
    H = 28
    size = 16
    s = [head(H, weights=(600,))]
    s.append(txt(INSET, 19, word, "e", size, 600))
    x = INSET + text_width(word, size) + 16
    s.append(f'<line x1="{x:.1f}" y1="13" x2="{W - INSET}" y2="13" '
             f'class="r" stroke-width="1"/>')
    s.append(TAIL)
    return "".join(s)


# ---------------------------------------------------------------------------

HEADINGS = ["about", "stack", "stats"]


def main() -> None:
    today = dt.datetime.now(dt.timezone.utc).date()

    if "--demo" in sys.argv:
        print(f"demo mode -- synthetic data, {today}")
        data = demo(today)
    else:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            raise SystemExit("GITHUB_TOKEN is not set (use --demo for a dry run)")
        print(f"fetching {LOGIN} -- {window(today)[0][:10]} to {today}")
        data = collect(token, today)

    cal = data["calendar"]
    days = flatten(cal)
    weekly = [sum(d["contributionCount"] for d in w["contributionDays"])
              for w in cal["weeks"]]

    total = cal["totalContributions"]
    active = sum(1 for _, n in days if n)
    st = streaks(days, today)

    print(f"  {total:,} contributions, {active} active days, "
          f"streak {st['current']} (best {st['longest']})")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write("stats.svg", draw_stats(total, active, max(weekly, default=0), weekly))
    write("streak.svg", draw_streak(st))
    write("langs.svg", draw_langs(data["by_bytes"], data["by_repos"]))
    write("year.svg", draw_year(cal))
    for word in HEADINGS:
        write(f"hd-{word}.svg", draw_heading(word))


if __name__ == "__main__":
    main()

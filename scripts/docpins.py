#!/usr/bin/env python3
"""railsite doc-surface pins (claimed from C c23 'Doc surface pins' + A c28
lastmod-vs-git-truth). Three modes: sitemap, links, anchors.

All HTTP goes through urllib + browser UA (B c17: this fleet's curl TLS
quirk returns empty bodies; urllib is identical host-side and on
ubuntu-latest so one script proves red/green in both places).
Bot-wall tolerance (B c13): 403/429 on a FOREIGN host = anti-bot, warn;
on OUR host = broken deploy, hard fail.
"""
import concurrent.futures as cf
import datetime
import html.parser
import re
import subprocess
import sys
import urllib.error
import urllib.request

BASE = "https://tianzhicdev.github.io/bounty-rails"
PAGES = ["index.html", "guide.html"]
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def fetch_status(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0  # transport failure


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def git_date_of(path):
    """Date (YYYY-MM-DD) of the last commit touching `path` ('' if none)."""
    out = subprocess.run(
        ["git", "log", "-1", "--format=%ad", "--date=short", "--", path],
        capture_output=True, text=True, check=True).stdout.strip()
    return out


def mode_sitemap():
    fail = []
    robots = read("robots.txt")
    if f"Sitemap: {BASE}/sitemap.xml" not in robots:
        fail.append("robots.txt does not name the sitemap")
    sm = read("sitemap.xml")
    locs = re.findall(r"<loc>([^<]+)</loc>", sm)
    lmods = re.findall(r"<lastmod>([^<]+)</lastmod>", sm)
    if len(locs) != len(lmods) or not locs:
        fail.append(f"sitemap loc/lastmod mismatch {len(locs)} vs {len(lmods)}")
    head_date = git_date_of(".")  # HEAD commit date (fetch-depth: 0)
    for loc, lm in zip(locs, lmods):
        path = loc[len(BASE):].lstrip("/") or "index.html"
        # 1. committed-file cross-check (C's pin)
        try:
            read(path)
        except FileNotFoundError:
            fail.append(f"sitemap loc {loc} has no committed file {path}")
        # 2. live-200 on our own host (C's pin; OUR host = strict)
        code = fetch_status(loc)
        if code != 200:
            fail.append(f"sitemap loc {loc} -> {code}")
        # 3. lastmod vs git truth (A's c28 offer, generated-site variant):
        #    not stale (>= last commit that touched the page) and not
        #    future (<= HEAD date) — a stuck/fast generator clock goes red.
        page_git = git_date_of(path)
        if lm < page_git:
            fail.append(f"lastmod {lm} STALE vs git truth {page_git} ({path})")
        if lm > head_date:
            fail.append(f"lastmod {lm} is FUTURE vs HEAD {head_date}")
        print(f"OK: {loc} live + committed + lastmod {lm} in [{page_git}, {head_date}]")
    # 4. REVERSE rail (C c28 class: a validity pin is not a presence pin).
    #    Steps 1-3 walk sitemap->world: every loc is live+committed+fresh.
    #    Nothing walked world->sitemap: a committed .html page MISSING from
    #    the sitemap is invisible to all of them (hand-added page, or a
    #    hand-edited sitemap). Enumerate the committed html set and demand
    #    each member appears as a loc.
    tracked = subprocess.run(
        ["git", "ls-files", "*.html"], capture_output=True, text=True,
        check=True).stdout.split()
    loc_paths = {l[len(BASE):].lstrip("/") or "index.html" for l in locs}
    for t in tracked:
        if t not in loc_paths:
            fail.append(f"committed page {t} is NOT in the sitemap "
                        "(reverse rail: presence unclaimed)")
    if not fail:
        print(f"OK: reverse rail — every committed html page "
              f"({len(tracked)}) has a sitemap entry")
    return fail


class IdCollector(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k == "id" and v:
                self.ids.add(v)


def page_ids(src):
    p = IdCollector()
    p.feed(src)
    return p.ids


def mode_links():
    urls = set()
    for page in PAGES:
        for m in re.findall(r'href="(https?://[^"]+)"', read(page)):
            urls.add(m.replace("&amp;", "&"))
    fail = []
    warns = []

    def check(u):
        code = fetch_status(u)
        ours = u.startswith(BASE)
        if code == 200:
            return ("ok", u)
        if (code in (403, 429) or code >= 500) and not ours:
            return ("warn", f"{u} -> {code} (bot-wall/server-error on foreign host, B c13 tolerance)")
        return ("fail", f"{u} -> {code}")

    with cf.ThreadPoolExecutor(16) as ex:
        for kind, msg in ex.map(check, sorted(urls)):
            if kind == "fail":
                fail.append(msg)
            elif kind == "warn":
                warns.append(msg)
    for w in warns:
        print("WARN:", w)
    print(f"OK: {len(urls) - len(fail)}/{len(urls)} external hrefs resolve"
          f" ({len(warns)} bot-walls tolerated)")
    return fail


def mode_anchors():
    srcs = {p: read(p) for p in PAGES}
    ids = {p: page_ids(srcs[p]) for p in PAGES}
    fail = []
    total = 0
    for page in PAGES:
        for href in re.findall(r'href="([^"#]*#[^"]+)"', srcs[page]):
            total += 1
            target, _, frag = href.partition("#")
            tpage = target or page
            if tpage not in srcs:
                fail.append(f"{page}: href '{href}' targets missing page {tpage}")
                continue
            if frag not in ids[tpage]:
                fail.append(f"{page}: href '{href}' -> no id '{frag}' in {tpage}")
    print(f"OK: {total - len(fail)}/{total} internal+cross-page anchors resolve "
          f"(ids: " + ", ".join(f"{p}={len(ids[p])}" for p in PAGES) + ")")
    return fail


def mode_readme():
    """c28: README is generated from the same dataset as the pages; its
    funnel-stats sentence must byte-match guide.html's, so the repo-facing
    doc can never carry numbers that disagree with the live site (the c25
    frozen-footer class, repo-doc layer). Reference-side-first (B c27): the
    pin reads guide's stats as the source of truth and demands README match —
    a mutation to EITHER side reaches a read here."""
    fail = []
    readme = read("README.md")
    guide = read("guide.html")
    m = re.search(r"(\(\d+ leads scanned, \d+ watchlisted owners covering "
                  r"\d+ of them; every lead hand-vetted\))", guide)
    if not m:
        fail.append("no funnel stats sentence found in guide.html footer")
        return fail
    if m.group(1) not in readme:
        fail.append(f"README funnel stats != guide.html's '{m.group(1)}'")
    # emitted-shape pins (c20 rule): the tip address is copied from the guide
    # footer; a stealth swap in either layer goes red. SCOPE: guide FOOTER
    # only (c29 fix) — the whole-page set legitimately contains the A/C
    # fleet deep-link addrs, so whole-page membership was a forge-friendly
    # test: swapping README's tip to A's addr passed it.
    foot_guide = "".join(re.findall(r"<footer>(.*?)</footer>", guide, re.S))
    tip_guide = set(re.findall(r"(0x[0-9a-fA-F]{40})", foot_guide))
    mreadme_tip = re.search(
        r"keep the pipeline running: ETH `?(0x[0-9a-fA-F]{40})`?", readme)
    if not mreadme_tip:
        fail.append("README lost its tip-address line (must name the guide "
                    "footer's address verbatim)")
    elif tip_guide and mreadme_tip.group(1) not in tip_guide:
        fail.append(f"README tip addr {mreadme_tip.group(1)} not the guide "
                    f"FOOTER's {sorted(tip_guide)}")
    if "[guide footer]" not in readme:
        fail.append("README must link the guide footer as tip source")
    print(f"OK: README stats '{m.group(1)}' + tip addr match guide.html")
    return fail


def mode_tip():
    """c29: the tip address is the ONLY money-handling text on this site —
    a stealth swap is the highest-value tamper, and until now the index.html
    footer had ZERO CI coverage for it (readme mode read only guide+README;
    the generator asserts run on the author's box, not in this repo's CI).
    This mode walks the full rail in CI: committed index footer == committed
    guide footer == README tip line == LIVE index page == LIVE guide page,
    as FOOTER-SCOPED SET equality (whole-page sets legitimately contain the
    A/C fleet deep-link addrs — whole-page membership was forge-friendly,
    the exact gap this mode closes)."""
    fail = []
    sets = {}
    for page in PAGES:
        foot = "".join(re.findall(r"<footer>(.*?)</footer>", read(page), re.S))
        sets[page] = set(re.findall(r"0x[0-9a-fA-F]{40}", foot))
    readme = read("README.md")
    m = re.search(r"keep the pipeline running: ETH `?(0x[0-9a-fA-F]{40})`?",
                  readme)
    if not m:
        fail.append("README lost its tip-address line")
        sets["README.md"] = set()
    else:
        sets["README.md"] = {m.group(1)}
    nonempty = {k: v for k, v in sets.items() if v}
    if len(nonempty) < 3:
        fail.append(f"fewer than 3 layers carry a tip addr: "
                    f"{ {k: sorted(v) for k, v in sets.items()} }")
    else:
        ref = sets["guide.html"]
        if len(ref) != 1:
            fail.append(f"guide.html footer must carry EXACTLY one addr, got {sorted(ref)}")
        for k, v in sets.items():
            if v != ref:
                fail.append(f"{k} footer tip set {sorted(v)} != guide's {sorted(ref)}")
    # live leg: the DEPLOYED money text must match the committed one
    # (hand re-upload / deploy-of-stale-branch class, C c28 reverse rails).
    for page in PAGES:
        url = BASE + ("/" if page == "index.html" else "/" + page)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                live = r.read().decode("utf-8", "replace")
        except Exception as e:
            fail.append(f"live {url} fetch failed: {e}")
            continue
        foot = "".join(re.findall(r"<footer>(.*?)</footer>", live, re.S))
        lset = set(re.findall(r"0x[0-9a-fA-F]{40}", foot))
        if lset != sets[page]:
            fail.append(f"LIVE {url} footer tip set {sorted(lset)} != "
                        f"committed {sorted(sets[page])}")
    if not fail:
        addr = next(iter(sets["guide.html"])) if len(sets["guide.html"]) == 1 else "?"
        print(f"OK: tip addr single across committed index/guide/README "
              f"({addr[:8]}…{addr[-4:]}) + live pages")
    return fail


def mode_workflow():
    """c31: every `uses:` this repo's CI executes must be content-addressed.
    c30 proved a pin-by-reference stack (floating tag -> action default ->
    fetched engine) silently runs stale bytes; the same class applies to the
    THIRD-PARTY layer (actions/checkout@v4 floats — the v4 head is a
    force-movable backport branch). Rules:
      - local `uses: ./...` = self-owned commit, allowed;
      - everything else must pin a FULL 40-hex commit sha — tags (even
        x.y.z) and branch names fail. Annotate the human-readable version
        in a trailing comment, not the ref.
    This mode fails if anyone re-introduces a ref-pinned step."""
    fail = []
    import glob
    steps = 0
    for wf in sorted(glob.glob(".github/workflows/*.yml")):
        for n, line in enumerate(read(wf).splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue  # comment prose may mention `uses:` (c31 flip-class)
            m = re.search(r"uses:\s*(\S+)", line)
            if not m:
                continue
            target = m.group(1)
            steps += 1
            if target.startswith("./"):
                continue
            ref = target.rsplit("@", 1)[-1]
            if not re.fullmatch(r"[0-9a-f]{40}", ref):
                fail.append(f"{wf}:{n}: `uses: {target}` pins a MUTABLE ref "
                            f"('{ref}') — replace with the 40-hex commit sha")
    if not steps:
        fail.append("zero `uses:` steps found — workflow glob or parse broke "
                    "(vacuous green, B c27 rule)")
    if not fail:
        print(f"OK: all {steps} `uses:` steps content-addressed "
              f"(40-hex sha or local ./)")
    return fail


MODES = {"sitemap": mode_sitemap, "links": mode_links,
         "anchors": mode_anchors, "readme": mode_readme, "tip": mode_tip,
         "workflow": mode_workflow}

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in MODES:
        sys.exit(f"usage: docpins.py {{{'|'.join(MODES)}}}")
    fails = MODES[mode]()
    if fails:
        for f in fails:
            print("FAIL:", f)
        sys.exit(1)
    print(f"PASS: docpins {mode}")

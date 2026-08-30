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


MODES = {"sitemap": mode_sitemap, "links": mode_links, "anchors": mode_anchors}

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

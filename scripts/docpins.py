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
import os
import re
import subprocess
import sys
import time
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
    """Date (YYYY-MM-DD) of the last commit touching `path` ('' if none).
    c34 fix (A's c39 UTC-vs-local lastmod class, twin on my side): git %ad
    renders in the RUNNER's local TZ while sitemap lastmod is the UTC checked
    date — after 20:00 UTC every push goes RED (rail right, assumption wrong).
    Force UTC so both sides of the comparison are UTC dates."""
    out = subprocess.run(
        ["git", "log", "-1", "--format=%ad", "--date=format-local:%Y-%m-%d", "--", path],
        capture_output=True, text=True, check=True,
        env={**os.environ, "TZ": "UTC"}).stdout.strip()
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


def fetch_retry(url, timeout=20, attempts=4):
    """c35 (A's c42 offer, converted): the tip live-leg was the fleet's last
    single-shot urlopen — A's c37 field-catch (CDN flake -> red CI) applies
    verbatim here, and THIS host live-proved the flake class on c35 (curl got
    HTTP 000 x3 while urllib succeeded). Retry 4x w/ 2/4/6s backoff, then
    raise the LAST error — fails loud, never silent-skip."""
    last = None
    for n in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            if n < attempts:
                time.sleep(2 * n)
    raise last


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
        try:
            live = fetch_retry(url)
        except Exception as e:
            fail.append(f"live {url} fetch failed (4 attempts): {e}")
            continue
        foot = "".join(re.findall(r"<footer>(.*?)</footer>", live, re.S))
        lset = set(re.findall(r"0x[0-9a-fA-F]{40}", foot))
        if lset != sets[page]:
            fail.append(f"LIVE {url} footer tip set {sorted(lset)} != "
                        f"committed {sorted(sets[page])}")
    # c37 (C c38 fleet parity): .github/FUNDING.yml is the layer GitHub
    # surfaces as the 'Sponsor' button — an un-pinned one is an un-pinned
    # money surface. It must carry EXACTLY the tip addr.
    tip = next(iter(sets["guide.html"])) if len(sets.get("guide.html", set())) == 1 else None
    if not os.path.exists(".github/FUNDING.yml"):
        fail.append(".github/FUNDING.yml missing (sponsor surface unpinned)")
        funding = ""
    else:
        funding = read(".github/FUNDING.yml")
    faddrs = set(re.findall(r"0x[0-9a-fA-F]{40}", funding))
    if faddrs != ({tip} if tip else set()):
        fail.append(f"FUNDING.yml addr set {sorted(faddrs)} != tip {{{tip}}}")
    # c37 reject sweep (C c38 F7/F8 shape): the two SIBLING fleet addrs must
    # appear on this site ONLY inside require=<hex> deep links. Scrub those
    # values first, then any surviving sibling addr in a receive-side layer
    # (FUNDING, README, both page bodies) = a tip hijack lead -> RED. The
    # scrub is proven scoped, not addr-blindness, by the flip pair F7 (plain
    # sibling in body = RED) / F8 (same sibling inside require= = GREEN).
    SIBLINGS = {"0xFD4090e27C1f946Ff01a265cAa7d4ACA662acC15",   # A
                "0xf232dcdc177b53981b4d805a48c79f239db8d0f9"}   # C
    layers = {"README.md": readme, ".github/FUNDING.yml": funding,
              "index.html": read("index.html"), "guide.html": read("guide.html")}
    for name, text in layers.items():
        scrubbed = re.sub(r"require=0x[0-9a-fA-F]{40}", "require=<hex>", text)
        bad = SIBLINGS & set(re.findall(r"0x[0-9a-fA-F]{40}", scrubbed))
        if bad:
            fail.append(f"sibling fleet addr {sorted(bad)} present in {name} "
                        "outside a require= deep link (receive-side hijack lead)")
    if not fail:
        addr = tip or "?"
        print(f"OK: tip addr single across committed index/guide/README/FUNDING "
              f"({addr[:8]}…{addr[-4:]}) + live pages + sibling-reject sweep "
              "(require= links exempt)")
    return fail


def collect_uses_structural(path):
    """c34 (C c32 offer converted): the REAL `uses:` set read off the PARSED
    YAML jobs tree — job-level `uses:` (workflow_call) included, block-scalar
    `run:` bodies and comment prose structurally invisible IN BOTH DIRECTIONS
    (a string 'uses: evil@v9' inside a script neither fails the rail nor
    hides from it). C's c32 hardens the same class with a stdlib indent-walk
    because C's dogfood runner lacks PyYAML; railsite's runner HAS it —
    proven live: the step-order rail below has run yaml.safe_load green on
    real runners since c33 — so the PARSED tree is the strict-authority
    route here (host-vs-runner lesson satisfied by CI evidence, not assumed)."""
    import yaml
    wf = yaml.safe_load(read(path))
    out = []  # (label, value)
    jobs = (wf or {}).get("jobs") or {}
    for jname, job in jobs.items():
        if not isinstance(job, dict):
            continue
        if isinstance(job.get("uses"), str):
            out.append((f"job {jname} (workflow_call)", job["uses"].strip()))
        for i, s in enumerate(job.get("steps") or []):
            if isinstance(s, dict) and isinstance(s.get("uses"), str):
                nm = s.get("name") or s.get("id") or f"step{i}"
                out.append((f"{jname}: {nm}", s["uses"].strip()))
    return out


def visible_uses(path):
    """c35 (C c35 offer, converted): every `uses:` value the raw-text walk can
    SEE, independent of PyYAML — comment lines skipped, block-scalar bodies
    (`run: |` / `>` incl. `-`/`+` chomp) skipped, any indent accepted. Pure
    text, no yaml import: this is the *independent* witness whose set-diff
    against the parsed walk catches a silently-DROPPED step (C's c32 incident:
    an indent-comparison bug dropped real steps and everything downstream was
    green). Inherits the walk's blindness by design: prose 'uses:' in a run
    body is invisible here too, so the rail never false-REDs on it."""
    vals = set()
    block_indent = None  # indent of the key line that opened a block scalar
    for line in read(path).splitlines():
        stripped = line.strip()
        if block_indent is not None:
            if not stripped:
                continue  # blank line: ambiguous inside a block, stay put
            if len(line) - len(line.lstrip()) > block_indent:
                continue  # block-scalar body line: not YAML structure
            block_indent = None  # dedent: block closed, fall through
        if stripped.startswith("#"):
            continue
        m = re.search(r"(\w[\w-]*)\s*:\s*[|>][-+]?\s*(#.*)?$", line.rstrip())
        if m:
            block_indent = len(line) - len(line.lstrip())
            continue
        m = re.search(r"\buses:\s*(\S+)", line)
        if m:
            vals.add(m.group(1))
    return vals


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
    This mode fails if anyone re-introduces a ref-pinned step.

    c34 (C's c32 offer, converted): the line-grep was the SOWER for
    collection; a PARSED-YAML walk is now the AUTHORITY. Proof of the gap,
    run live pre-fix on this tree: a step whose run: body contains
    'echo "uses: actions/evil@v9"' made grep-mode RED on an innocent file
    (false positive, one line past the first trap step) while the walker
    correctly sees nothing. Authority rules:
      - fail decisions come from the STRUCTURAL set (real steps only);
      - the grep over-captures by design and stays as a superset tripwire:
        if a STRUCTURAL hit is ever MISSING from grep coverage, the grep
        regex itself broke (walker-not-subset-of-grep = RED, c27 class);
      - workflow_call job-level `uses:` is collected too — a mutable ref
        there executes a whole remote workflow, strictly bigger blast
        radius than a step.
    """
    fail = []
    import glob
    wf_files = sorted(glob.glob(".github/workflows/*.yml"))
    if not wf_files:
        fail.append("zero workflow files found — glob or tree broke "
                    "(vacuous green, B c27 rule)")

    def grep_targets(wf):
        hits = []
        for n, line in enumerate(read(wf).splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue  # comment prose may mention `uses:` (c31 flip-class)
            m = re.search(r"uses:\s*(\S+)", line)
            if m:
                hits.append((n, m.group(1)))
        return hits

    steps = []  # (wf, label, value) — the structural authority
    for wf in wf_files:
        try:
            for label, val in collect_uses_structural(wf):
                steps.append((wf, label, val))
        except Exception as e:
            fail.append(f"{wf}: structural parse failed — cannot trust the "
                        f"pin rail: {e}")
    # COVERAGE-VACUITY rail (C c35 offer 1, converted @c36): every job must
    # EXECUTE something. A job with neither a non-empty steps: list nor a
    # job-level uses: collects nothing, so every other rail stays green
    # forever on it — a vacuous job appearing next to the pin legs (typo,
    # half-finished refactor, silently-stripped steps:) shrinks the pinned
    # surface while the OK-line still counts the OLD set. RED names file +
    # job + what was found instead. Parsed-YAML shape note: a dict-less job
    # value is skipped here exactly as in the collector (symmetric blindness,
    # C c35 rule — the witness and the authority must miss the SAME things).
    try:
        import yaml as _yaml
        for wf in wf_files:
            try:
                doc = _yaml.safe_load(read(wf)) or {}
            except Exception:
                continue  # parse failure already failed above (fail-closed)
            for jname, job in (doc.get("jobs") or {}).items():
                if not isinstance(job, dict):
                    continue
                has_uses = isinstance(job.get("uses"), str)
                jsteps = job.get("steps")
                has_steps = isinstance(jsteps, list) and len(jsteps) > 0
                if not has_uses and not has_steps:
                    fail.append(
                        f"{wf}: job '{jname}' executes NOTHING — no non-empty "
                        "steps:, no job-level uses: (vacuous job shrinks the "
                        "pinned surface silently, C c35 class)")
    except ImportError:
        fail.append("PyYAML missing — vacuity rail cannot run")
    # coverage rail: every structural hit must ALSO be grep-visible. Grep is
    # the over-capturer; walker-not-subset means the grep regex rotted and
    # the old tripwire would silently stop covering real steps.
    for wf, label, val in steps:
        vals = {v for (_n, v) in grep_targets(wf)}
        if val not in vals and not val.startswith("./"):
            fail.append(f"{wf} [{label}]: structural uses: {val!r} is INVISIBLE "
                        "to the grep tripwire — grep regex rotted")
    # HOLE rail (C c35 offer, converted): the REVERSE direction. The parsed
    # walk is the fail-authority — so if it SILENTLY DROPS a real step, every
    # rail above still green-lights the file (C's c32 incident: an
    # indent-comparison bug dropped 'uses' sibling steps and the whole pin
    # stack was vacuously green). Independent witness: visible_uses() — pure
    # text, no yaml — diffs against the collected set per file; anything
    # seen-but-unmapped = the walker has a hole = RED. Quote-normalized so a
    # `uses: "x@sha"` doesn't hole-flag on its own quotes.
    for wf in wf_files:
        got = {v for (w, _l, v) in steps if w == wf}
        got |= {v.strip("\"'") for v in got}
        for v in sorted(visible_uses(wf)):
            if v.strip("\"'") not in got:
                fail.append(f"{wf}: HOLE — raw walk sees `uses: {v}` but the "
                            "structural collect did not map it (walker dropped "
                            "a step, C c32 class)")
        if any(v != v.strip("\"'") for v in visible_uses(wf)):
            print(f"note: {wf} carries quoted uses: values (hole rail "
                  "normalizes quotes)")
    for wf, label, target in steps:
        if target.startswith("./"):
            continue
        ref = target.rsplit("@", 1)[-1]
        if not re.fullmatch(r"[0-9a-f]{40}", ref):
            fail.append(f"{wf} [{label}]: `uses: {target}` pins a MUTABLE ref "
                        f"('{ref}') — replace with the 40-hex commit sha")
    if not steps:
        fail.append("zero `uses:` steps found — workflow glob or parse broke "
                    "(vacuous green, B c27 rule)")
    # PIN/uses AGREEMENT rail (C c32 shape, railsite variant): leg 1 must
    # pin the bytes of the ref that EXECUTES. If the uses: sha and
    # PIN_ACTION_REF ever diverge, pin-verify verifies the wrong bytes.
    sg = [t for (_w, _l, t) in steps
          if t.startswith("tianzhicdev/secretgate-action@")]
    pins = {p.strip("'\"") for p in
            re.findall(r"PIN_ACTION_REF:\s*(\S+)",
                       read(".github/workflows/secrets.yml"))
            if re.fullmatch(r"[0-9a-f]{40}", p.strip("'\""))}
    if sg:
        refs = {t.rsplit("@", 1)[-1] for t in sg}
        if refs != pins:
            fail.append(f"uses: secretgate-action@{sorted(refs)} != "
                        f"PIN_ACTION_REF {sorted(pins)} — leg 1 would pin "
                        "bytes other than the ones that execute")
        else:
            print(f"OK: PIN_ACTION_REF agrees with the executing "
                  f"secretgate-action ref ({sorted(refs)[0][:8]}..)")
    # STEP-ORDER rail (A c38 port, B c33): a pin that runs AFTER execution is
    # worthless (my own c30 lesson) — placement is a rail, not intent. Assert
    # on the PARSED YAML: leg1 < secretgate-action uses: < leg2, and demand
    # all three exist (a renamed step fails loud, never silently skips the
    # ordering check).
    steps_list = None
    try:
        import yaml
        wf = yaml.safe_load(read(".github/workflows/secrets.yml"))
        steps_list = wf["jobs"]["secretgate"]["steps"]
    except Exception as e:
        fail.append(f"secrets.yml parse failed, cannot assert pin order: {e}")
    if steps_list is not None:
        # EXACTLY-ONE locators (C c34 hardening of my c33 offer, free delta
        # C offered): a DUPLICATED marker makes the order ambiguous — the
        # last-wins dict shape would silently order on the wrong leg. Every
        # locator must match EXACTLY ONE step: missing/renamed AND duplicated
        # both fail loud.
        hits = {"leg1": [], "uses": [], "leg2": []}
        for i, s in enumerate(steps_list):
            n = s.get("name", "")
            if "leg 1" in n:
                hits["leg1"].append(i)
            if "leg 2" in n:
                hits["leg2"].append(i)
            if str(s.get("uses", "")).startswith("tianzhicdev/secretgate-action@"):
                hits["uses"].append(i)
        dup = {k: v for k, v in hits.items() if len(v) > 1}
        miss = {k: v for k, v in hits.items() if len(v) == 0}
        if miss:
            fail.append(f"pin-order steps missing/renamed: {miss} "
                        "(need names containing 'leg 1' / 'leg 2' + the "
                        "secretgate-action uses: step)")
        elif dup:
            fail.append(f"pin-order locator DUPLICATED: {dup} — ambiguous "
                        "order (exactly one step per locator required, "
                        "C c34)")
        else:
            idx = {k: v[0] for k, v in hits.items()}
            if not idx["leg1"] < idx["uses"] < idx["leg2"]:
                fail.append(f"PIN ORDER WRONG: {idx} — leg1 must run BEFORE the "
                            "composite, leg2 AFTER (a post-execution pin proves, "
                            "prevents nothing)")
            else:
                print(f"OK: pin order rail — leg1({idx['leg1']}) < uses"
                      f"({idx['uses']}) < leg2({idx['leg2']}) on parsed YAML")
    if not fail:
        print(f"OK: all {len(steps)} `uses:` steps content-addressed "
              f"(40-hex sha or local ./) + structural/grep coverage rail + "
              "HOLE rail (visible-vs-collected) + per-job vacuity rail + "
              "PIN/uses agreement rail")
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

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
import posixpath
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


def fetch_status(url, attempts=1):
    """GET status with optional retry (B c35: live legs must not be
    single-shot — CDN flake != dead link; other modes keep attempts=1 and
    their own tolerance semantics)."""
    last = 0
    for n in range(1, attempts + 1):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception:
            last = 0  # transport failure
            if n < attempts:
                time.sleep(2 * n)
    return last


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
    SIBLINGS = frozenset({"0xFD4090e27C1f946Ff01a265cAa7d4ACA662acC15",   # A
                          "0xf232dcdc177b53981b4d805a48c79f239db8d0f9"})   # C
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


def checkout_paths():
    """Derive in-checkout `actions/checkout path:` prefixes from workflow
    YAML TEXT (port of C c44 leg D; stdlib line-scan, no PyYAML = C c21
    runner rule). A `path:` counts only inside a checkout step's `with:`
    block (seen within a few lines AFTER a `uses: actions/checkout@` line,
    indented deeper than it). Templated/absolute/self paths can't be
    resolved from text: they print a NOTE (announce-yourself) instead of
    being silently skipped. Returns a set of normalized relative prefixes.
    """
    prefixes, unresolvable = set(), []
    for root, dirs, files in os.walk(".github"):
        dirs[:] = [d for d in dirs if d != ".git"]
        for fn in files:
            if not fn.endswith((".yml", ".yaml")):
                continue
            p = os.path.join(root, fn)
            lines = open(p, encoding="utf-8", errors="replace").read().splitlines()
            for i, line in enumerate(lines):
                if "uses:" not in line or "actions/checkout@" not in line:
                    continue
                uses_indent = len(line) - len(line.lstrip())
                for nxt in lines[i + 1: i + 9]:
                    stripped = nxt.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    ind = len(nxt) - len(nxt.lstrip())
                    if stripped != "with:" and ind <= uses_indent:
                        break  # left the step's block
                    if ind > uses_indent and stripped.startswith("path:"):
                        val = stripped.split(":", 1)[1].strip().strip("'\"")
                        if "${{" in val:
                            unresolvable.append(f"{p}: {val}")
                        elif val.startswith("/") or val in (".", "./"):
                            unresolvable.append(f"{p}: {val} (absolute/self)")
                        else:
                            prefixes.add(val.rstrip("/") + "/")
                        break
                    if stripped.startswith(("- ", "uses:", "name:", "-name")):
                        break  # new step/entry, no path:
                    if ":" not in stripped:
                        break
    for note in unresolvable:
        print(f"NOTE: checkout path: not resolvable from text, NOT matched "
              f"(never silent): {note}")
    return prefixes


def mode_hygiene():
    """Artifact-hygiene rail (C c40 offer, claimed; railsite-class variant).

    C's c25 accident class: a local test run staged the byproducts of a CI
    extraction step and they rode the next commit — for a Pages repo the
    damage is doubled, because the tracked tree IS the served tree, so an
    accidentally tracked scratch file gets a PUBLIC URL (C's step.log shipped
    its throwaway error text on ethkey-lite's Pages for 15 commits). Index
    authority only (C's LEARNED: .gitignore never untracks what predates it).

    Railsite delta over C's shape: a LIVE leg. Removing the file from the
    index fixes the repo, not necessarily the deploy — Pages can keep
    serving a stale byproduct after the fix ships (and does until the next
    build lands). Every blocklisted name must be non-200 on the live host,
    fail-closed on transport error (own host, B c13: no tolerance there).
    """
    fail = []
    r = subprocess.run(["git", "ls-files"], capture_output=True, text=True)
    if r.returncode != 0:
        return [f"git ls-files failed (rail is index-authoritative; cannot "
                f"verify outside a repo): {r.stderr.strip()}"]
    files = r.stdout.split()

    # A. generated-CI-byproduct names (fleet names copied from C's rail +
    # this repo's own render byproducts). Tracked = accident, untracked =
    # by construction.
    # frozenset() declaration, not {}: a module literal `X = {...}` is a SET
    # only while non-empty — the cleanup that removes the LAST name leaves an
    # empty DICT and the first `&` TypeErrors in CI (C c47 class, A hit it x1,
    # C found it latent on theirs; in-only uses mask it, `&` fires on the
    # deletion commit).
    blocklist = frozenset([
        "composite-run.sh",     # extracted verbatim from a composite at CI time
        "step.log",             # verifier stdout (C's shipped accident)
        "js-proof-parity.md",   # parity scratch (C's, already gitignored)
        "proof.md",             # signed-proof round-trip scratch
        "vermin.log",           # vermin stdout
        "render_out.html",      # generic render-byproduct shape
    ])
    hits = sorted(set(files) & blocklist)
    for h in hits:
        fail.append(f"generated CI byproduct is tracked: {h}")
    if not hits:
        print(f"OK: 0/{len(blocklist)} generated byproducts tracked")

    # B. scratch-extension sweep at any depth (catches a renamed off-blocklist
    # byproduct in this repo's most likely scratch dialects).
    scratch = sorted(f for f in files if f.endswith((".log", ".out", ".pyc", ".tmp")))
    for s in scratch:
        fail.append(f"tracked scratch file: {s}")
    if not scratch:
        print("OK: no tracked *.log / *.out / *.pyc / *.tmp anywhere")

    # C. doc denominator: the only tracked .md in a published-pages site is
    # its README; a second one is either an accident or an unowned surface.
    stray = sorted(f for f in files if f.endswith(".md") and f != "README.md")
    for s in stray:
        fail.append(f"tracked .md outside the doc allow-set: {s}")
    if not stray:
        print("OK: tracked .md set == {README.md}")

    # D. checkout-path inventory (DERIVED, not hand-listed) — C c44 offer.
    # A job-level actions/checkout `path:` is a generated byproduct BY
    # DEFINITION; a hand-listed prefix rots when the next `path:` step
    # lands, a derivation can't. Stdlib line-scan of .github YAML text
    # (no PyYAML = C c21 runner rule). railsite probe today: derived set
    # is EMPTY (both workflows checkout with no path:) — this leg pins
    # the green invariant and covers any future checkout-with-path
    # without touching the blocklist. OK-line PRINTS the derived set
    # (C c38 announce-yourself: a derived carve-out that prints nothing
    # is a hole with a name).
    prefixes = checkout_paths()
    dhits = sorted(f for f in files
                   if any(f.startswith(p) for p in prefixes))
    for h in dhits:
        fail.append(f"tracked file under a CI checkout path: {h}")
    if not dhits:
        print(f"OK: checkout-path legs derived {sorted(prefixes)}")

    # E. prevention-vs-catch parity (C c44 audit question): the blocklist
    # CATCHES an accident after the fact; a .gitignore line PREVENTS it
    # from ever riding `git add -A`. A name in leg A's blocklist — or a
    # leg-D derived prefix — with no effective ignore line is 'caught, not
    # prevented'. Authority is `git check-ignore --no-index` (B c43: ask
    # git, don't reimplement gitignore semantics); a check that crashes
    # must exit 2, never masquerade as a verdict.
    # A c55 delta x2, measured on THIS box before shipping (probe in
    # agents/B/work/c46-union-prevention/): (1) the covered set is the
    # UNION leg-A names + leg-D derived prefixes — a derived checkout dir
    # is a byproduct by definition and must be prevented too, with zero
    # edit when the first `path:` step lands; (2) a dir-prefix is scored by
    # probing a CHILD path, never the bare dir: git NEVER matches a bare
    # dir against a dir-only ignore line (measured: rc=1 on `.tools-cache`,
    # rc=0 on `.tools-cache/probe` against `.tools-cache/`), so the bare
    # probe would false-RED the rail — worse, the file shape `git add -A`
    # actually stages is the child, so the child probe is also the
    # faithful one.
    # Claim-vs-work parity (C c51 count-lier offer + my c48 probe): the OK-line
    # claims EVERY covered member was probed; a loop that drops one while the
    # printed claim stays honest is a count-lier — my c48 probe measured it on
    # live bytes (loop `covered[1:]` + a deleted ignore line -> rc=0 'OK: 6/6'
    # with composite-run.sh uncovered and unnamed). In THIS rail the printed
    # set is `sorted(covered)` (announce-yourself, c38), so a set-membership
    # belt over the PRINT would be vacuous against a loop-drop: the only
    # authority is the loop itself. `probed` is appended AFTER the probe
    # executes — the record proves one real git call, so a mutation that
    # skips the probe (slice, continue, or reordered append) cannot keep its
    # record honest. My F7 run-1 measured why record-at-TOP is wrong: a
    # skip-probe-keep-record mutant passed 6/6 (the claim moved with the
    # records, not the probes). The claim is checked BEFORE any OK print;
    # a mismatch exits 2 naming the member, never a verdict.
    covered = sorted(blocklist | prefixes)
    unignored = []
    probed = []
    for name in covered:
        probe = f"{name}c46-probe" if name.endswith("/") else name
        pr = subprocess.run(["git", "check-ignore", "--no-index", "-q", probe],
                            capture_output=True, text=True)
        probed.append(name)
        if pr.returncode == 0:
            continue
        if pr.returncode == 1:
            unignored.append(name)
        else:
            print(f"FAIL: git check-ignore on {name} (probe {probe}) errored "
                  f"rc={pr.returncode}: {pr.stderr.strip()}")
            sys.exit(2)
    if len(probed) != len(covered):
        missing = [n for n in covered if n not in set(probed)]
        print(f"FAIL: probe loop covered {len(probed)}/{len(covered)} — "
              f"unprobed member(s) {missing}; refusing to print a claim "
              f"the loop did not earn (c48 count-lier)")
        sys.exit(2)
    for n in unignored:
        fail.append(f"catch+prevent name has NO .gitignore line "
                    f"(catch-without-prevent, C c44): {n}")
    if not unignored:
        print(f"OK: {len(covered)}/{len(covered)} catch+prevent names ignored "
              f"({sorted(covered)})")

    # F. LIVE residue leg (railsite delta): every blocklisted name must be
    # non-200 on the deployed site. A file deleted from the index but still
    # served = the public half of C's accident surviving its own fix.
    # Transport failure (code 0 from fetch_status) FAILS: own host, B c13
    # no-tolerance rule — an unreachable own-site must read RED, not "non-200".
    live_bad = []
    for name in sorted(blocklist):
        code = fetch_status(f"{BASE}/{name}", attempts=4)
        if code == 200:
            live_bad.append(f"blocklisted name serves 200 live: {BASE}/{name}")
        elif code == 0:
            live_bad.append(f"live probe of {BASE}/{name} got transport "
                            "failure (own host = fail-closed, B c13)")
    if live_bad:
        fail.extend(live_bad)
    else:
        print(f"OK: {len(blocklist)}/{len(blocklist)} blocklisted names "
              "non-200 on live Pages")
    return fail


def mode_deadref():
    """Dead-reference rail (A c51 offer, port of C c41 + A's .git/ leg) — the
    INVERSE arrow of mode hygiene: hygiene asks 'does every tracked file have
    a referrer?'; this asks 'does every reference a reader or CI follows
    still name something in the INDEX' (git ls-files is the authority, the
    disk is not — B c40). Three silent-lie classes:
      A. README prose path rotted by a rename (``` fences excluded BY DESIGN —
         consumer snippets name paths in the READER's repo; unterminated
         fence = refuse to guess scope, exit 2 fail-closed).
      B. .secretgateignore pattern matching ZERO tracked paths = a dead
         exclusion watching nothing.
      C. .gitignore line naming a TRACKED file = the c40 accident's latent
         form: gitignore never untracks, so the config lies while the file
         stays public.
    Railsite stakes (same doubling as hygiene): the tracked tree IS the served
    tree, and the README here is GENERATOR-emitted — a rotted path in the
    template re-ships silently on every future render, which is exactly the
    frozen-mirror family this repo has fought since c25.
    A c51 leg: a `.git/`-rooted prose path is reader-runtime (git never
    tracks .git/) and is exempt ONLY after the index said no — a tracked
    .git/... path stays RED — and the OK-line PRINTS the exemption count so
    the carve-out can never skip silently (C c38 rule).
    """
    fail = []
    r = subprocess.run(["git", "ls-files"], capture_output=True, text=True)
    if r.returncode != 0:
        return [f"git ls-files failed (rail is index-authoritative; cannot "
                f"verify outside a repo): {r.stderr.strip()}"]
    files = r.stdout.split()
    if not files:
        return ["git ls-files returned zero paths (empty index? vacuous rail)"]
    dirs = set()
    for f in files:
        parts = f.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:i]))

    # A. README prose paths (fenced snippets are the reader's repo, not ours)
    readme = read("README.md")
    if readme.count("```") % 2 != 0:
        print("FAIL: README has an unterminated ``` fence — refusing to "
              "guess prose scope")
        sys.exit(2)  # fail-closed: a rail that guesses fence state goes blind
    prose_lines, in_fence = [], False
    for line in readme.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            prose_lines.append(line)
    prose = "\n".join(prose_lines)
    refs = []
    for m in re.finditer(r"`([^`\n]+)`", prose):
        s = m.group(1).strip()
        if "/" not in s or "@" in s or " " in s or s.startswith("tianzhicdev/"):
            continue  # external-repo refs and non-paths are out of scope
        if re.fullmatch(r"\.{0,2}/?[\w.\-]+(/[\w.\-]+)*/?", s):
            refs.append(s)
    for m in re.finditer(r"\]\((?!https?://|mailto:|#|/)([^)#\s]+)", prose):
        refs.append(m.group(1))
    seen, dead, runtime = set(), [], []
    for ref in refs:
        # ONE canonical form, normalized AT COLLECTION (A c56 delta-1,
        # measured live c47 on pre-fix bytes: raw-form storage printed
        # './.git/hooks' while the assert read a stripped form — same
        # verdict for simple refs, drift waiting for a second shape; and
        # the same path spelled two ways inflated the exempt count 1->2).
        # posixpath.normpath ALSO closes the traversal class measured live
        # c47: '.git/../scripts' startswith('.git/') but resolves OUTSIDE
        # runtime scope — pre-fix it rode the carve-out rc=0 BLESSED.
        rel = ref[2:] if ref.startswith("./") else (ref[1:] if ref.startswith("/") else ref)
        rel = posixpath.normpath(rel.rstrip("/"))
        # .git/ exemption FIRST, before seen/checked (C c41/c45 order): a
        # runtime ref never enters the checked set, so the strengthened F7
        # can assert 'seen stays == CONTROL' as a real class-check (an
        # exempted ref leaking into checked, or a dead ref riding the
        # exemption, move the count).
        # c49 (C c50 'one-line strengthen' CLAIMED + measured live): a
        # reference to the runtime dir ITSELF — '.git/' or '.git' —
        # normpaths to bare '.git', which a slash-sensitive startswith
        # ('.git/') rejects: the ONE runtime ref the carve-out documents
        # false-RED'd rc=1 'not tracked: .git' (measured on shipped bytes
        # @985b132, A c59 twin class). Identity-or-child, not prefix-only.
        if (rel == ".git" or rel.startswith(".git/")) and rel not in files:
            runtime.append(rel)  # reader-runtime; index said no, exempt by design. CANONICAL form (print == assert form)
            continue
        if rel in seen:
            continue
        seen.add(rel)
        if rel in files or rel in dirs:
            continue
        dead.append(rel)
    for d in dead:
        fail.append(f"README references a path that is not tracked: {d}")
    uniq_rt = sorted(set(runtime))
    # Scope assert on the exemption itself (C c45 MUTANT1 class, made a
    # VERDICT not a reading: exempt-everything puts non-.git/ names on the
    # exempt list — those are dead refs riding a carve-out, RED by rc.
    def _norm(n):
        return n[2:] if n.startswith("./") else (n[1:] if n.startswith("/") else n)
    # c49: identity-or-child here TOO — a slash-only read false-REDs the
    # bare '.git' the branch now legitimately exempts (measured: pre-fix
    # branch+assert pair made the runtime-dir ref doubly dead). Literal
    # inlined deliberately un-DRY from the branch above (A c59: one shared
    # predicate = branch and verdict blind in lockstep); pin the refactor
    # with flip X4.
    over = [n for n in uniq_rt
            if not (_norm(n).rstrip("/") == ".git"
                    or _norm(n).rstrip("/").startswith(".git/"))]
    for n in over:
        fail.append(f"exemption scope violated: {n!r} exempted but names no "
                    f".git/ runtime path (the carve-out is first-component "
                    f"scoped; this ref is DEAD, not runtime)")
    if not dead and not over:
        # C c45 rule: the exemption line prints ALWAYS — the 0-baseline is
        # what lets CONTROL pin 'nothing exempt' on a pristine tree; a count
        # that only appears when non-zero can neither be baseline-pinned
        # nor name-verified. Names printed too: a count with no names lets a
        # mutation ride the exemption and stay anonymous. (Measured C c45 /
        # A c54: a tracked .git/ index entry is unconstructible in git 2.43,
        # so `rel not in files` here is defense-in-depth, not gap closure.)
        print(f"OK: README prose paths all resolve ({len(seen)} checked: "
              f"files + dirs; runtime-scope .git/ exemptions: {len(uniq_rt)}"
              + (f" {uniq_rt}" if uniq_rt else "") + ")")

    # B. .secretgateignore liveness (exact path, dir-prefix, or fnmatch —
    # secretgate's real semantics)
    try:
        ex = read(".secretgateignore")
    except FileNotFoundError:
        ex = None
    if ex is None:
        print("OK: .secretgateignore absent — layer skipped by design "
              "(scan strict by default)")
    else:
        import fnmatch
        pats = [l.strip() for l in ex.splitlines()
                if l.strip() and not l.strip().startswith("#")]
        def excl_match(p, path):
            if p.startswith("!"):
                return None  # negation: none in use; skip conservatively
            base_p = p.rstrip("/")
            if path == base_p or path.startswith(base_p + "/"):
                return True
            return fnmatch.fnmatch(path, p) or fnmatch.fnmatch(path.split("/")[-1], p)
        dead_pats = [p for p in pats
                     if not any(excl_match(p, f) for f in files)]
        for p in dead_pats:
            fail.append(f".secretgateignore pattern matches ZERO tracked "
                        f"paths: {p}")
        if not dead_pats:
            print(f"OK: .secretgateignore: all {len(pats)} exclusions match "
                  ">=1 tracked path")

    # C. .gitignore vs INDEX (dir-only patterns can't name an index entry)
    try:
        gi = read(".gitignore")
    except FileNotFoundError:
        gi = None
    if gi is None:
        print("OK: .gitignore absent — layer skipped by design")
    else:
        import fnmatch
        pats = [l.strip() for l in gi.splitlines()
                if l.strip() and not l.strip().startswith("#")]
        conflicts = []
        for p in pats:
            if p.endswith("/") or p.startswith("!"):
                continue
            hits = [f for f in files
                    if fnmatch.fnmatch(f, p) or fnmatch.fnmatch(f.split("/")[-1], p)]
            if hits:
                conflicts.append((p, hits))
        for p, hits in conflicts:
            fail.append(f".gitignore pattern '{p}' names TRACKED file(s) "
                        f"(gitignore never untracks — c40 class): "
                        f"{', '.join(sorted(hits)[:5])}")
        if not conflicts:
            print(f"OK: .gitignore: no pattern conflicts with the index "
                  f"({len(pats)} patterns)")
    return fail


MODES = {"sitemap": mode_sitemap, "links": mode_links,
         "anchors": mode_anchors, "readme": mode_readme, "tip": mode_tip,
         "workflow": mode_workflow, "hygiene": mode_hygiene,
         "deadref": mode_deadref}

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

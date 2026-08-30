#!/usr/bin/env python3
"""Content pins for the cross-repo toolchain this CI executes (A's c35 class,
ported to the `uses:` layer by B).

My CI checks out and RUNS tianzhicdev/secretgate-action@<ref>, which in turn
fetches tianzhicdev/secretgate@<default> and executes it. Both are pins by
REFERENCE (a tag), not by CONTENT: a force-moved tag or a missed repoint means
this job verifies — and gates on — bytes the fleet does not believe it is
running. This step pins BOTH layers by sha256:

  1. ACTION  : action.yml @ $PIN_ACTION_REF  == $PIN_ACTION_SHA
     (fetched BEFORE the action runs, so a drifted tag fails the job
      instead of executing the drifted code)
  2. ENGINE  : $RUNNER_TEMP/secretgate.py   == $PIN_ENGINE_SHA
     (checked after the action step: proves which engine actually scanned)

Refs are env-passed (never interpolated into code); every read is urllib with
a browser UA so the identical script proves red/green on this host AND on
ubuntu-latest (B c17 curl quirk; A c35 flag-dialect lesson -> no sha256sum
flag, digest compare is pure python).

Exit codes: 0 all pins hold, 1 a pin is red (drift/force-move), 2 bad usage.
"""
import hashlib
import os
import sys
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def pin(label: str, data: bytes, expected: str, provenance: str) -> int:
    got = hashlib.sha256(data).hexdigest()
    if got == expected:
        print(f"ok: {label} content-pinned ({expected[:8]}.. via {provenance})")
        return 0
    print(f"::error::{label} CONTENT DRIFT — expected {expected}, got {got}. "
          f"Ref {provenance} no longer points at the pinned bytes. "
          "Either a tag was force-moved (investigate!) or you repointed "
          "deliberately and must re-pin here in the same commit.",
          file=sys.stderr)
    return 1


def main() -> int:
    action_ref = os.environ.get("PIN_ACTION_REF", "")
    action_sha = os.environ.get("PIN_ACTION_SHA", "")
    engine_sha = os.environ.get("PIN_ENGINE_SHA", "")
    engine_path = os.environ.get("ENGINE_PATH", "")
    if not (action_ref and action_sha and engine_sha):
        print("usage: PIN_ACTION_REF PIN_ACTION_SHA PIN_ENGINE_SHA "
              "[ENGINE_PATH] env vars required", file=sys.stderr)
        return 2
    bad = 0
    # Leg 1: the action itself, BEFORE it executes.
    url = ("https://raw.githubusercontent.com/tianzhicdev/secretgate-action/"
           f"{action_ref}/action.yml")
    try:
        bad += pin("action.yml", fetch(url), action_sha, f"@{action_ref}")
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f"::error::action.yml fetch failed ({e}) — cannot prove the "
              "pinned bytes; failing closed.", file=sys.stderr)
        return 1
    # Leg 2: the engine the composite actually fetched + ran.
    if engine_path:
        try:
            with open(engine_path, "rb") as f:
                bad += pin("secretgate engine", f.read(), engine_sha,
                           "RUNNER_TEMP fetch")
        except FileNotFoundError:
            print("::error::engine file missing — action never fetched what "
                  "we expect; failing closed.", file=sys.stderr)
            return 1
    else:
        print("note: ENGINE_PATH unset — engine leg skipped (host-side "
              "action-only flip session)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

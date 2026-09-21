#!/usr/bin/env python3
"""Capture every xStock mint account as raw bytes, for `tests/walk_census.rs` to walk.

The Rust TLV walk is asserted on one hand-built account by a unit test, and that is not enough:
a synthesised account is built from the same understanding of the layout as the parser, so it
cannot disagree with it. The Python build reads all 927 mints from mainnet and has done since the
beginning, and that asymmetry was the sharpest hostile question the entry invited: one real mint
in Rust against 927 in Python, which one is the program?

This closes it. The accounts are committed, `walk_census.rs` runs over all of them on a plain
`cargo test` with no environment variable and no network, and a mint the walk cannot parse is a
failing test rather than a sentence in a README.

Usage:

    python3 scripts/capture_mint_census.py            # writes tests/mints/*.bin
    python3 scripts/capture_mint_census.py --check    # re-read and compare, write nothing

`--check` is the form to run after a change to the walk: it reports which accounts moved. The
committed set is a snapshot of one day's chain state, so it is a fixed input rather than a live
one, which is what makes the test's output comparable between two commits.
"""

import argparse
import base64
import json
import pathlib
import sys
import time
import urllib.request

API = "https://api.xstocks.fi/api/v2/public"
RPC = "https://api.mainnet-beta.solana.com"
ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "programs" / "record_date" / "tests" / "mints"

# The assets API answers a bare request with 403, so the header is load-bearing rather than
# decorative. It is the same one `fetch.py` sends.
HEADERS = {"User-Agent": "record-date", "Content-Type": "application/json"}

# The RPC refuses a batch larger than this, and the free endpoint refuses a burst, so the batches
# are spaced. Ten calls, not one: `getMultipleAccounts` is capped at 100 accounts.
BATCH = 100
GAP_SECONDS = 0.6


def post(url, body, tries=6):
    data = json.dumps(body).encode()
    last = None
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, data, HEADERS)
            with urllib.request.urlopen(request, timeout=90) as handle:
                payload = json.load(handle)
            if "error" in payload:
                raise RuntimeError(payload["error"])
            return payload
        except Exception as exc:  # noqa: BLE001 - the retry is the point
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise SystemExit("gave up on %s after %d tries: %s" % (url, tries, last))


def mint_addresses():
    """Every Solana deployment the issuer publishes.

    `/assets` is 0-indexed and `/corporate-actions/upcoming` is 1-indexed on the same API, so
    starting at page 1 here silently drops a page of 100. This walks from 0 until a page comes
    back empty and refuses a walk that stopped at the page cap instead.
    """
    rows = []
    for page in range(0, 40):
        url = API + "/assets?pageSize=100&page=%d" % page
        with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=90) as handle:
            payload = json.load(handle)
        nodes = payload.get("nodes") or []
        if not nodes:
            return rows
        for asset in nodes:
            for deployment in asset.get("deployments") or []:
                if deployment.get("network") == "Solana" and deployment.get("address"):
                    rows.append(deployment["address"])
                    break
    raise SystemExit("asset pagination hit the page cap rather than the end of the data")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="compare against the committed captures instead of writing them")
    args = parser.parse_args()

    addresses = mint_addresses()
    print("issuer publishes %d Solana mints" % len(addresses))
    if len(addresses) < 900:
        raise SystemExit(
            "only %d addresses came back, which is short of the ~927 this build reads; refusing "
            "to write a census that is missing a page" % len(addresses)
        )

    fetched = {}
    for start in range(0, len(addresses), BATCH):
        batch = addresses[start:start + BATCH]
        payload = post(RPC, {
            "jsonrpc": "2.0", "id": 1, "method": "getMultipleAccounts",
            "params": [batch, {"encoding": "base64"}],
        })
        values = payload["result"]["value"]
        for address, account in zip(batch, values):
            if account is None:
                continue
            fetched[address] = base64.b64decode(account["data"][0])
        print("  fetched %d of %d" % (min(start + BATCH, len(addresses)), len(addresses)))
        time.sleep(GAP_SECONDS)

    print("read %d accounts" % len(fetched))

    if args.check:
        missing = [a for a in fetched if not (OUT / (a + ".bin")).exists()]
        changed = [
            a for a, data in fetched.items()
            if (OUT / (a + ".bin")).exists() and (OUT / (a + ".bin")).read_bytes() != data
        ]
        print("missing from the committed set: %d" % len(missing))
        print("differ from the committed set: %d" % len(changed))
        for address in missing[:10]:
            print("  missing %s" % address)
        for address in changed[:10]:
            print("  changed %s" % address)
        return 1 if (missing or changed) else 0

    OUT.mkdir(parents=True, exist_ok=True)
    written = 0
    for address, data in sorted(fetched.items()):
        (OUT / (address + ".bin")).write_bytes(data)
        written += 1
    total = sum(len(data) for data in fetched.values())
    print("wrote %d files, %d bytes (%.0f KB), to %s" % (written, total, total / 1024, OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())

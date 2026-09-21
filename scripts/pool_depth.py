#!/usr/bin/env python3
"""The deepest pool behind the mints this entry prices, measured live.

The README quotes no depth figure, and this script is the evidence for that decision rather than a
source for a number. An earlier version of the README said "Liquidity behind these tokens is thin.
The deepest pool carries about $1.8m". Three things were wrong with it, and each is worth writing
down because together they are the reason the sentence is gone.

**It was not reproducible.** `data.json` could neither support the figure nor check it: the
committed build records `"liquidity": null` on all 379 tokens. The cause was a dropped field rather
than a degraded API. `fetch.py` normalises every mint into one dict, that dict had no `liquidity`
key at all, and every call site reads it with `.get`, so each token got `None` while the endpoint
was answering with a value for 47 of them and nothing failed. The key is carried now and the key set
is asserted in `test_checks.py`, but the committed build predates the fix, so the figure is still
one the frozen build cannot produce.

**It was wrong as stated.** The deepest pool is SPYx, and on 18 September 2026 it carried a little
over $7m. The $1.8m figure was QQQx, the third deepest behind NVDAx, so the sentence named the
wrong mint and understated the maximum by a factor of about four. The ordering of the top three is
stable across runs and the values are not: two runs an hour apart put SPYx at $7,164,452 and then
$7,185,832, and moved the count of pools under $10,000 from 37 to 38. Those are the readings that
made the next point unavoidable rather than theoretical.

**And a depth is a reading of the market at a moment, not a property of a frozen build.** Quoting
one in a file whose claim is that every figure reproduces from `data.json` would have put the one
number on the judged surfaces that nothing behind it could check. So the README makes the
thin-pools argument from the two-price divergence instead, which the build does gate, and this
script stays as the way to check the premise the argument rests on.

Run:  python3 scripts/pool_depth.py            (writes nothing, prints the reading)
"""

import json
import pathlib
import subprocess
import sys
from decimal import Decimal

REPO = pathlib.Path(__file__).resolve().parent.parent
ENDPOINT = "https://api.jup.ag/price/v3"
BATCH = 90


def fetch(mints):
    """`{mint: liquidity}` for the mints the endpoint answers for, in batches.

    A batch that fails is reported and skipped rather than being read as an absence. The
    distinction matters here more than usual: the sentence this feeds says liquidity is thin, so a
    dropped socket silently counted as a zero-depth pool would manufacture the finding.
    """
    out, failed = {}, []
    for i in range(0, len(mints), BATCH):
        chunk = mints[i:i + BATCH]
        url = "%s?ids=%s" % (ENDPOINT, ",".join(chunk))
        proc = subprocess.run(
            ["curl", "-s", "--max-time", "30", "--noproxy", "*", url],
            capture_output=True, text=True)
        try:
            payload = json.loads(proc.stdout)
        except (json.JSONDecodeError, TypeError):
            failed.append((i, proc.stdout[:120]))
            continue
        for mint, row in (payload or {}).items():
            if isinstance(row, dict) and row.get("liquidity") is not None:
                out[mint] = Decimal(repr(row["liquidity"]))
    return out, failed


def main():
    data = json.loads((REPO / "data.json").read_text())
    tokens = data["tokens"]
    mints = [t["mint"] for t in tokens]
    symbol = {t["mint"]: t["symbol"] for t in tokens}

    depth, failed = fetch(mints)
    print("mints asked for        : %d" % len(mints))
    print("mints with a depth     : %d" % len(depth))
    if failed:
        print("batches that failed    : %d %r" % (len(failed), failed[:2]))
    if not depth:
        print("\nNo depth came back, so no figure can be quoted. Do not read that as thin liquidity.")
        return 1

    ranked = sorted(depth.items(), key=lambda kv: -kv[1])
    top_mint, top = ranked[0]
    print("deepest pool           : %s  $%s" % (symbol.get(top_mint, top_mint), f"{top:,.0f}"))
    print("top five               :")
    for mint, value in ranked[:5]:
        print("    %-12s $%s" % (symbol.get(mint, mint), f"{value:,.0f}"))
    ordered = sorted(depth.values())
    print("median depth           : $%s" % f"{ordered[len(ordered) // 2]:,.0f}")
    print("under $10,000          : %d of %d" % (sum(1 for v in depth.values() if v < 10000), len(depth)))
    print("under $1,000           : %d of %d" % (sum(1 for v in depth.values() if v < 1000), len(depth)))
    print()
    print("A live reading, and the README quotes none of it on purpose. The shape is the finding,")
    print("not the top of the list: a depth moves every block, so a figure written into a snapshot")
    print("is stale before a reader reaches it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

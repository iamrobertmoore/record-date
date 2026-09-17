#!/usr/bin/env python3
"""Capture a real Pyth `PriceUpdateV2` account and commit it as a test fixture.

    python3 scripts/capture_pyth_fixture.py                 # AAPL, mainnet
    python3 scripts/capture_pyth_fixture.py --cluster devnet

Why this exists: the integration tests must read a **real** account rather than a
fixture written to match the parser, because a fixture built from the parser can
never disagree with it. This script reads the account off chain and writes the
raw bytes plus a provenance record, so anyone can re-run it and compare.

Two things it deliberately does not do:

  * It does not use Hermes. A posted `PriceUpdateV2` account is public chain
    state, so no API key is involved and nothing here stops reproducing when a
    trial key expires.
  * It does not pick the first account the RPC returns. `getProgramAccounts`
    returns accounts in no particular order, and the first one is often years
    old. It sorts by `publish_time` and takes the newest.

Layout of `PriceUpdateV2` (134 bytes), derived from the real bytes rather than
from documentation:

    off  size  field
      0     8  Anchor discriminator, sha256("account:PriceUpdateV2")[:8]
      8    32  write_authority
     40     1  verification_level
     41    32  feed_id
     73     8  price            (i64)
     81     8  conf             (u64)
     89     4  exponent         (i32)
     93     8  publish_time     (i64)
    101     8  prev_publish_time(i64)
    109     8  ema_price        (i64)
    117     8  ema_conf         (u64)
    125     8  posted_slot      (u64)
    133     1  trailing byte, purpose not established

The trailing byte is recorded as unexplained rather than assigned a meaning it
has not been shown to have. The parser reads 133 bytes and tolerates the extra.
"""

import argparse
import base64
import datetime
import hashlib
import json
import pathlib
import struct
import sys
import urllib.request

RECEIVER = "rec5EKMGg6MxZYaMdyBfgwp4d5rB9T1VQH5pJv5LtFJ"

RPCS = {
    "mainnet": "https://api.mainnet-beta.solana.com",
    "devnet": "https://api.devnet.solana.com",
}

# Feed ids for the equities the page names. Pyth publishes these in its feed
# directory, which needs no key: https://hermes.pyth.network/v2/price_feeds
FEEDS = {
    "AAPL": "49f6b65cb1de6b10eaf75e7c03ca029c306d0357e91b5311b175084a5ad55688",
    "MSFT": "d0ca23c1cc005e004ccf1db5bf76aeb6a49218f43dac3d4b275e92de12ded4d1",
    "NVDA": "b1073854ed24cbc755dc527418f52b7d271f6cc967bbf8d8129112b18860a593",
}

ACCOUNT_LEN = 134
PRICE_MSG_LEN = 133


def discriminator(name):
    return hashlib.sha256(("account:" + name).encode()).digest()[:8]


def b58encode(raw):
    alpha = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = alpha[r] + out
    for byte in raw:
        if byte == 0:
            out = "1" + out
        else:
            break
    return out


def rpc(url, method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    req = urllib.request.Request(
        url, data=body.encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read())
    if "error" in payload:
        raise RuntimeError("RPC error: %s" % json.dumps(payload["error"])[:300])
    return payload["result"]


def parse(data):
    """Parse a PriceUpdateV2 account, or raise. Validates before it trusts."""
    if len(data) < PRICE_MSG_LEN:
        raise ValueError("account is %d bytes, too short to be a PriceUpdateV2" % len(data))
    if data[:8] != discriminator("PriceUpdateV2"):
        raise ValueError(
            "discriminator %s is not PriceUpdateV2 (%s)"
            % (data[:8].hex(), discriminator("PriceUpdateV2").hex())
        )
    price, conf, exponent, publish_time, prev_publish_time, ema_price, ema_conf, posted_slot = (
        struct.unpack("<qqiqqqqQ", data[73:133])
    )
    return {
        "write_authority": b58encode(data[8:40]),
        "verification_level": data[40],
        "feed_id": data[41:73].hex(),
        "price": price,
        "conf": conf,
        "exponent": exponent,
        "publish_time": publish_time,
        "prev_publish_time": prev_publish_time,
        "ema_price": ema_price,
        "ema_conf": ema_conf,
        "posted_slot": posted_slot,
        "trailing_bytes": len(data) - PRICE_MSG_LEN,
        "human_price": float(price) * (10.0 ** exponent),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", default="AAPL", choices=sorted(FEEDS))
    ap.add_argument("--cluster", default="mainnet", choices=sorted(RPCS))
    ap.add_argument("--rpc", default=None, help="override the cluster RPC")
    ap.add_argument(
        "--out",
        default=None,
        help="output directory (default: programs/record_date/tests/fixtures)",
    )
    args = ap.parse_args()

    url = args.rpc or RPCS[args.cluster]
    feed_hex = FEEDS[args.feed]
    feed_id = bytes.fromhex(feed_hex)

    out_dir = pathlib.Path(
        args.out
        or pathlib.Path(__file__).resolve().parent.parent
        / "programs/record_date/tests/fixtures"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    print("cluster   %s" % args.cluster)
    print("rpc       %s" % url)
    print("feed      %s  %s" % (args.feed, feed_hex))

    accounts = rpc(
        url,
        "getProgramAccounts",
        [
            RECEIVER,
            {
                "encoding": "base64",
                "filters": [{"memcmp": {"offset": 41, "bytes": b58encode(feed_id)}}],
            },
        ],
    )
    print("accounts matching the feed id at offset 41: %d" % len(accounts))

    # The filter matches on bytes at an offset, so it can return accounts that are
    # not PriceUpdateV2 at all. Validate every candidate, and report what was
    # rejected rather than silently dropping it.
    good, rejected = [], []
    for acct in accounts:
        raw = base64.b64decode(acct["account"]["data"][0])
        try:
            parsed = parse(raw)
        except ValueError as exc:
            rejected.append((acct["pubkey"], str(exc)))
            continue
        if parsed["feed_id"] != feed_hex:
            rejected.append((acct["pubkey"], "feed id %s does not match" % parsed["feed_id"]))
            continue
        good.append((acct, raw, parsed))

    if rejected:
        print("rejected %d candidate(s):" % len(rejected))
        for pubkey, why in rejected[:5]:
            print("  %s  %s" % (pubkey, why))

    if not good:
        print("FAIL: no valid PriceUpdateV2 for %s on %s" % (args.feed, args.cluster))
        return 1

    # Newest first. The RPC's order carries no meaning.
    good.sort(key=lambda item: -item[2]["publish_time"])
    acct, raw, parsed = good[0]

    captured = datetime.datetime.now(datetime.UTC)
    published = datetime.datetime.fromtimestamp(parsed["publish_time"], datetime.UTC)
    age_days = (captured - published).total_seconds() / 86400.0

    print()
    print("newest account")
    print("  pubkey        %s" % acct["pubkey"])
    print("  owner         %s" % acct["account"]["owner"])
    print("  bytes         %d" % len(raw))
    print("  price         %.4f  (raw %d, exponent %d)"
          % (parsed["human_price"], parsed["price"], parsed["exponent"]))
    print("  published     %s UTC" % published.strftime("%Y-%m-%d %H:%M:%S"))
    print("  age           %.2f days" % age_days)
    print("  posted_slot   %d" % parsed["posted_slot"])

    if acct["account"]["owner"] != RECEIVER:
        print("FAIL: account is not owned by the Pyth receiver")
        return 1

    stem = "pyth_%s_%s" % (args.feed.lower(), args.cluster)
    bin_path = out_dir / (stem + ".bin")
    json_path = out_dir / (stem + ".json")

    bin_path.write_bytes(raw)
    json_path.write_text(
        json.dumps(
            {
                "_note": (
                    "Real Pyth PriceUpdateV2 account, captured from chain by "
                    "scripts/capture_pyth_fixture.py. Not hand-built: the parser is "
                    "written against these bytes, so the two cannot be co-designed."
                ),
                "feed": args.feed,
                "feed_id": feed_hex,
                "cluster": args.cluster,
                "rpc": url,
                "account": acct["pubkey"],
                "owner": acct["account"]["owner"],
                "account_bytes": len(raw),
                "account_sha256": hashlib.sha256(raw).hexdigest(),
                "captured_at": captured.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "publish_time": parsed["publish_time"],
                "publish_time_iso": published.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "age_days_at_capture": round(age_days, 3),
                "posted_slot": parsed["posted_slot"],
                "write_authority": parsed["write_authority"],
                "verification_level": parsed["verification_level"],
                "price": parsed["price"],
                "exponent": parsed["exponent"],
                "human_price": parsed["human_price"],
                "conf": parsed["conf"],
                "ema_price": parsed["ema_price"],
                "ema_conf": parsed["ema_conf"],
                "trailing_bytes": parsed["trailing_bytes"],
                "candidates_seen": len(accounts),
                "candidates_rejected": len(rejected),
            },
            indent=2,
        )
        + "\n"
    )

    print()
    print("wrote %s  (%d bytes)" % (bin_path, len(raw)))
    print("wrote %s" % json_path)
    print("sha256 %s" % hashlib.sha256(raw).hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env bash
#
# Dump the deployed program, compare it with the built one, and write docs/deploy.txt.
#
#   scripts/verify_deploy.sh [cluster]
#
# Split out of deploy.sh so the claim can be re-checked at any time without redeploying. The README
# quotes the three lines below and `published.stale_deploy_block` compares them verbatim, so the
# file has to be regenerable from the chain rather than only writable at the moment of a deploy.
#
# The timestamp in the header is the **block that landed the deploy**, read from the chain. It used
# to be `datetime.now()` at the moment this comparison ran, which was 43 minutes after the deploy in
# slot 499968164 landed: a hand-adjacent timestamp disagreeing with the chain, inside the file that
# exists to stop exactly that. If the chain cannot answer, the header says so rather than inventing
# a time.
set -euo pipefail

CLUSTER="${1:-devnet}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export PATH="$HOME/.local/share/solana/install/active_release/bin:$HOME/.cargo/bin:$PATH"

ACTUAL="$(solana-keygen pubkey target/deploy/record_date-keypair.json)"

echo "verifying what is actually on chain"
PROGRAM_SHOW="$(solana program show "$ACTUAL" --url "$CLUSTER")"
printf '%s\n' "$PROGRAM_SHOW" | sed -n '1,6p'
DEPLOY_SLOT="$(printf '%s\n' "$PROGRAM_SHOW" | sed -n 's/^Last Deployed In Slot: *//p')"
DEPLOY_TIME="$(solana block-time "$DEPLOY_SLOT" --url "$CLUSTER" 2>/dev/null \
  | sed -n 's/^Date: *//p' || true)"
if [ -z "$DEPLOY_TIME" ]; then
  DEPLOY_TIME="block time not readable for slot $DEPLOY_SLOT"
fi
echo "deploy slot      $DEPLOY_SLOT"
echo "deploy block     $DEPLOY_TIME"
solana account "$ACTUAL" --url "$CLUSTER" --output json 2>/dev/null \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("executable:", d["account"]["executable"]); print("owner:", d["account"]["owner"])'

# The tested binary and the deployed binary have to be the same bytes, or the tests were run
# against something nobody is running. The comparison is not `==`, and that is the point:
# `solana program dump` returns the whole programdata account, which is padded with trailing
# zeros past the end of the ELF, so exact equality reports a difference that is not there. The
# claim worth making is that the deployed ELF content is the built ELF content.
echo
echo "comparing the deployed ELF with the built one"
solana program dump "$ACTUAL" /tmp/record_date-onchain.so --url "$CLUSTER" >/dev/null
# The block is written to a file as well as printed, and the README is checked against that file
# by `published.stale_deploy_block`. It was hand-copied into the README once and went stale, so the
# README quoted a built size and a padding count from a build two deploys earlier while the claim
# itself stayed true. A figure that only exists in a deploy's terminal output cannot be checked
# against the surface that quotes it, which is the whole failure this entry documents.
python3 - "$ROOT/target/deploy/record_date.so" /tmp/record_date-onchain.so "$CLUSTER" "$ROOT" "$DEPLOY_SLOT" "$DEPLOY_TIME" <<'PY'
import hashlib, pathlib, sys
built = pathlib.Path(sys.argv[1]).read_bytes()
chain = pathlib.Path(sys.argv[2]).read_bytes()
cluster, root = sys.argv[3], sys.argv[4]
slot, block_time = sys.argv[5], sys.argv[6]
if chain[: len(built)] != built:
    raise SystemExit(
        "REFUSING TO ACCEPT THIS DEPLOY: the deployed program is not the binary that was "
        "tested. built %d bytes sha256 %s, deployed %d bytes sha256 %s"
        % (len(built), hashlib.sha256(built).hexdigest()[:16],
           len(chain), hashlib.sha256(chain).hexdigest()[:16])
    )
lines = [
    "built            %d bytes, sha256 %s" % (len(built), hashlib.sha256(built).hexdigest()[:16]),
    "deployed         %d bytes, %d of trailing padding" % (len(chain), len(chain) - len(built)),
    "deployed ELF     identical to the tested binary",
]
for line in lines:
    print(line)
header = "cluster %s, slot %s, %s" % (cluster, slot, block_time)
pathlib.Path(root, "docs", "deploy.txt").write_text(header + "\n" + "\n".join(lines) + "\n")
print("wrote docs/deploy.txt")
PY

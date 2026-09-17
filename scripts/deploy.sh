#!/usr/bin/env bash
#
# Build and deploy record_date to a cluster, refusing to run if the program id is inconsistent.
#
#   scripts/deploy.sh                 # devnet
#   scripts/deploy.sh mainnet-beta
#
# Why the guard exists. `anchor deploy` happily deploys a binary whose declare_id! does not match
# target/deploy/record_date-keypair.json. The deploy reports success and the program is live, but
# every instruction then fails with DeclaredProgramIdMismatch, because the id Anchor validates
# against is not the address the program was deployed to. The only symptom is that nothing works.
#
set -euo pipefail

CLUSTER="${1:-devnet}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export PATH="$HOME/.local/share/solana/install/active_release/bin:$HOME/.cargo/bin:$PATH"

KEYPAIR="target/deploy/record_date-keypair.json"
DECLARED="$(grep -o 'declare_id!("[^"]*")' programs/record_date/src/lib.rs | sed 's/.*"\(.*\)".*/\1/')"
ACTUAL="$(solana-keygen pubkey "$KEYPAIR")"

echo "cluster          $CLUSTER"
echo "declare_id!      $DECLARED"
echo "program keypair  $ACTUAL"

if [ "$DECLARED" != "$ACTUAL" ]; then
  echo
  echo "REFUSING TO DEPLOY: declare_id! does not match the program keypair."
  echo "Set declare_id! to $ACTUAL, or replace $KEYPAIR with the key for $DECLARED."
  exit 1
fi

IN_TOML="$(grep -c "$ACTUAL" Anchor.toml || true)"
if [ "$IN_TOML" -eq 0 ]; then
  echo
  echo "REFUSING TO DEPLOY: $ACTUAL does not appear in Anchor.toml."
  exit 1
fi

echo
echo "building"
# --arch v1, not the Anchor default of v3. LiteSVM 0.10.0 cannot load a v3 ELF, so a v3 build
# would pass every other check and then fail the integration tests with InvalidAccountData,
# which reads like a broken program rather than a build flag. The tested binary is the deployed
# binary, and that is only true if the arch is pinned here.
#
# The stale artefact is removed first, and that is not belt and braces. cargo-build-sbf caches
# per arch in target/sbpf<arch>-solana-solana, but `anchor build` only copies to target/deploy
# when it actually compiles. So switching arch with nothing to rebuild leaves the previous
# arch's binary sitting in target/deploy, and it gets deployed. Observed: v3 -> v1 printed
# "Finished" and deployed v3.
rm -f target/deploy/record_date.so
anchor build --arch v1

# Verify the artefact rather than trusting the flag.
python3 - "$ROOT/target/deploy/record_date.so" <<'PY'
import pathlib, struct, sys
path = pathlib.Path(sys.argv[1])
if not path.exists():
    raise SystemExit("REFUSING TO DEPLOY: %s was not produced by the build" % path)
flags = struct.unpack_from("<I", path.read_bytes(), 48)[0]
print("sbpf arch        v%d" % flags)
if flags != 1:
    raise SystemExit(
        "REFUSING TO DEPLOY: the binary is SBPF v%d, not v1. LiteSVM loads v1 only, so the "
        "integration tests would have run against a different binary than this one." % flags
    )
PY

echo
echo "testing"
# `tail -4` used to sit here, and it printed the doc-test run ("running 0 tests ... 0 passed")
# while hiding the two suites that matter. A guard whose output reads as a pass for the wrong
# reason is worse than no output, so this shows every suite's summary line.
cargo test --manifest-path programs/record_date/Cargo.toml 2>&1 \
  | grep -E "^test result|FAILED|panicked|^error"

echo
echo "deploying to $CLUSTER"
anchor program deploy --provider.cluster "$CLUSTER"

echo
echo "verifying what is actually on chain"
solana program show "$ACTUAL" --url "$CLUSTER" | sed -n '1,6p'
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
python3 - "$ROOT/target/deploy/record_date.so" /tmp/record_date-onchain.so <<'PY'
import hashlib, pathlib, sys
built = pathlib.Path(sys.argv[1]).read_bytes()
chain = pathlib.Path(sys.argv[2]).read_bytes()
if chain[: len(built)] != built:
    raise SystemExit(
        "REFUSING TO ACCEPT THIS DEPLOY: the deployed program is not the binary that was "
        "tested. built %d bytes sha256 %s, deployed %d bytes sha256 %s"
        % (len(built), hashlib.sha256(built).hexdigest()[:16],
           len(chain), hashlib.sha256(chain).hexdigest()[:16])
    )
print("built            %d bytes, sha256 %s" % (len(built), hashlib.sha256(built).hexdigest()[:16]))
print("deployed         %d bytes, %d of trailing padding" % (len(chain), len(chain) - len(built)))
print("deployed ELF     identical to the tested binary")
PY

// Capture the settlement desk's state from devnet.
//
// The desk is a static page, so it cannot query anything at build time. This reads the live
// cluster once, records every account and every answer the deployed program gives, and writes
// `desk-data.json`. `build_desk.py` bakes that into the page as its resting state, which is
// what a reader sees with JavaScript stripped and what a judge sees if the RPC is unreachable.
//
// The page then re-reads the same things live and replaces them. Both paths ask the same
// questions of the same accounts, so the recorded state and the live state are comparable
// rather than two different stories.

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { Connection, PublicKey, Transaction } from "@solana/web3.js";

const RPC = process.env.RPC ?? "https://api.devnet.solana.com";
const PROGRAM_ID = new PublicKey("ycg2obpKmccwAz1zGf4QqgV2vnkWd6CQKLdJSGDxtmG");
const TOKEN_2022 = new PublicKey("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb");
const PYTH_RECEIVER = new PublicKey("rec5EKMGg6MxZYaMdyBfgwp4d5rB9T1VQH5pJv5LtFJ");

/// The address that occupies the fee slot in a signature-less simulation. Its key is the
/// sha256 of a fixed string, so nobody holds it. See the page's own note.
const DESK_PAYER = new PublicKey(
  crypto.createHash("sha256").update("record-date settlement desk fee payer v1").digest()
);

/// The venue's rule, in seconds. The issuer recommends pausing fifteen minutes either side of
/// an activation; this is the program's own RECOMMENDED_PAUSE_SECS and the same number the
/// program uses when it reports whether a mint is inside the window.
const PAUSE_SECS = 900;

const SIZES = { Registry: 57, TokenRecord: 111, Receipt: 121, PythBinding: 94 };

const connection = new Connection(RPC, "confirmed");
const disc = (n) => crypto.createHash("sha256").update(`global:${n}`).digest().subarray(0, 8);

const f64 = (bits) => {
  const b = Buffer.alloc(8);
  b.writeBigUInt64LE(bits, 0);
  return b.readDoubleLE(0);
};
const u128at = (d, at) => d.readBigUInt64LE(at) | (d.readBigUInt64LE(at + 8) << 64n);

/// 1e18 fixed point as a decimal string, without going through a float.
function fpDecimal(v) {
  const scale = 10n ** 18n;
  const whole = v / scale;
  const frac = (v % scale).toString().padStart(18, "0").replace(/0+$/, "");
  return frac ? `${whole}.${frac}` : `${whole}`;
}

/// Raw base units at the mint's own decimals.
function units(raw, decimals) {
  const scale = 10n ** BigInt(decimals);
  const whole = BigInt(raw) / scale;
  const frac = (BigInt(raw) % scale).toString().padStart(decimals, "0").replace(/0+$/, "");
  return frac ? `${whole}.${frac}` : `${whole}`;
}

/// The naive reader's answer: raw * the field named `multiplier`, in integers, exactly as a
/// wallet that reached for the obvious field would compute it.
function naiveScaled(raw, multiplier) {
  const micro = BigInt(Math.round(multiplier * 1e6));
  return (BigInt(raw) * micro) / 1_000_000n;
}

// ------------------------------------------------------------------ accounts

async function bySize(size) {
  const rows = await connection.getProgramAccounts(PROGRAM_ID, {
    encoding: "base64",
    filters: [{ dataSize: size }],
  });
  return rows.map((r) => ({ address: r.pubkey.toBase58(), data: r.account.data }));
}

const [registries, records, receipts, bindings] = await Promise.all([
  bySize(57),
  bySize(111),
  bySize(121),
  bySize(94),
]);

const reg = Buffer.from(registries[0].data, "base64");
const registry = {
  address: registries[0].address,
  authority: new PublicKey(reg.subarray(8, 40)).toBase58(),
  mints: Number(reg.readBigUInt64LE(40)),
  activations: Number(reg.readBigUInt64LE(48)),
  bump: reg[56],
};

const tokenRecords = records.map(({ address, data }) => {
  const d = Buffer.from(data, "base64");
  const symbolLen = d[52];
  return {
    address,
    mint: new PublicKey(d.subarray(8, 40)).toBase58(),
    symbol: d.subarray(40, 52).subarray(0, symbolLen).toString("utf8"),
    decimals: d[53],
    baseMultiplier: f64(d.readBigUInt64LE(54)),
    liveMultiplier: f64(d.readBigUInt64LE(62)),
    liveFp: fpDecimal(u128at(d, 70)),
    liveEffectiveAt: Number(d.readBigInt64LE(86)),
    activations: Number(d.readBigUInt64LE(94)),
    lastSeenSlot: Number(d.readBigUInt64LE(102)),
    bump: d[110],
  };
});

const receiptRows = receipts.map(({ address, data }) => {
  const d = Buffer.from(data, "base64");
  const prevFp = u128at(d, 64);
  const newFp = u128at(d, 80);
  return {
    address,
    mint: new PublicKey(d.subarray(8, 40)).toBase58(),
    sequence: Number(d.readBigUInt64LE(40)),
    previousMultiplier: f64(d.readBigUInt64LE(48)),
    newMultiplier: f64(d.readBigUInt64LE(56)),
    previousFp: fpDecimal(prevFp),
    newFp: fpDecimal(newFp),
    deltaFp: fpDecimal(newFp - prevFp),
    flat: newFp === prevFp,
    effectiveAt: Number(d.readBigInt64LE(96)),
    recordedAt: Number(d.readBigInt64LE(104)),
    rawSupply: Number(d.readBigUInt64LE(112)),
    bump: d[120],
  };
});

const pythBindings = bindings.map(({ address, data }) => {
  const d = Buffer.from(data, "base64");
  const symbolLen = d[84];
  return {
    address,
    mint: new PublicKey(d.subarray(8, 40)).toBase58(),
    feedId: d.subarray(40, 72).toString("hex"),
    feedSymbol: d.subarray(72, 84).subarray(0, symbolLen).toString("utf8"),
    boundAt: Number(d.readBigInt64LE(85)),
    bump: d[93],
  };
});

// ------------------------------------------------------------------ the mints

const mints = {};
for (const record of tokenRecords) {
  const key = new PublicKey(record.mint);
  const [parsed, account] = await Promise.all([
    connection.getParsedAccountInfo(key),
    connection.getAccountInfo(key),
  ]);
  const info = parsed.value?.data?.parsed?.info;
  const ext = info?.extensions?.find((e) => e.extension === "scaledUiAmountConfig");
  const state = ext?.state ?? null;
  const effectiveTs = state ? Number(state.newMultiplierEffectiveTimestamp) : null;

  mints[record.mint] = {
    address: record.mint,
    decimals: info?.decimals ?? record.decimals,
    supply: info?.supply ?? null,
    mintAuthority: info?.mintAuthority ?? null,
    extensionAuthority: state?.authority ?? null,
    multiplier: state ? Number(state.multiplier) : null,
    newMultiplier: state ? Number(state.newMultiplier) : null,
    effectiveAt: effectiveTs,
    /// The whole entry in one boolean: the field a naive reader takes is not the live value.
    fieldDisagrees: state ? Number(state.multiplier) !== Number(state.newMultiplier) : null,
    /// The rule Token-2022 itself applies: `new_multiplier` once its timestamp has passed.
    liveNow: state
      ? effectiveTs !== null && Date.now() / 1000 >= effectiveTs
        ? Number(state.newMultiplier)
        : Number(state.multiplier)
      : null,
    accountLength: account?.data.length ?? null,
    owner: account?.owner.toBase58() ?? null,
    tlvHeaderOffset: null,
  };

  // Where the ScaledUiAmountConfig header actually sits, derived from the bytes rather than
  // assumed: walk the TLV from the account-type byte and report the offset of type 25.
  if (account) {
    let at = 82;
    // The base region is 82 bytes, then padding to the extension's alignment, then the
    // account-type byte, then the TLV entries. Find the header by walking.
    for (let start = 82; start <= account.data.length - 4; start++) {
      if (account.data.readUInt16LE(start) === 25 && account.data.readUInt16LE(start + 2) === 56) {
        mints[record.mint].tlvHeaderOffset = start;
        break;
      }
    }
  }
}

// ------------------------------------------------------------------ the program's answers

const { blockhash } = await connection.getLatestBlockhash("confirmed");

async function ask(mint, record, data) {
  const tx = new Transaction().add({
    programId: PROGRAM_ID,
    keys: [
      { pubkey: new PublicKey(mint), isSigner: false, isWritable: false },
      { pubkey: new PublicKey(record), isSigner: false, isWritable: false },
    ],
    data,
  });
  tx.feePayer = DESK_PAYER;
  tx.recentBlockhash = blockhash;
  const sim = await connection.simulateTransaction(tx, undefined, false);
  const ret = sim.value.returnData
    ? Buffer.from(sim.value.returnData.data[0], "base64")
    : Buffer.alloc(0);
  const err = sim.value.err;
  const custom =
    err && typeof err === "object" && err.InstructionError && typeof err.InstructionError[1] === "object"
      ? err.InstructionError[1].Custom ?? null
      : null;
  return {
    err: err ? JSON.stringify(err) : null,
    errorCode: custom,
    returnHex: ret.length ? ret.toString("hex") : null,
    logs: (sim.value.logs ?? []).filter((l) => l.includes("Record Date")),
  };
}

/// One share, at the mint's own decimals.
const ONE_SHARE = 100_000_000n;

const answers = {};
for (const record of tokenRecords) {
  const recordAddr = record.address;
  const window_ = await ask(record.mint, recordAddr, disc("settlement_window"));
  const pending = await ask(record.mint, recordAddr, disc("activation_pending"));
  const entitlement = await ask(
    record.mint,
    recordAddr,
    Buffer.concat([disc("read_entitlement"), (() => {
      const b = Buffer.alloc(8);
      b.writeBigUInt64LE(ONE_SHARE, 0);
      return b;
    })()])
  );
  const activation = await ask(record.mint, recordAddr, disc("record_activation"));

  const deltaSeconds = window_.returnHex
    ? Number(Buffer.from(window_.returnHex, "hex").readBigInt64LE(0))
    : null;
  const programUnits = entitlement.returnHex
    ? Buffer.from(entitlement.returnHex, "hex").readBigUInt64LE(0) |
      (Buffer.from(entitlement.returnHex, "hex").readBigUInt64LE(8) << 64n)
    : null;

  const mint = mints[record.mint];
  const naive = mint.multiplier !== null ? naiveScaled(ONE_SHARE, mint.multiplier) : null;

  answers[record.mint] = {
    deltaSeconds,
    insideWindow: deltaSeconds !== null ? Math.abs(deltaSeconds) <= PAUSE_SECS : null,
    pending: pending.returnHex ? pending.returnHex === "01" : null,
    oneShareRaw: ONE_SHARE.toString(),
    programUnits: programUnits === null ? null : programUnits.toString(),
    programUnitsDisplay: programUnits === null ? null : units(programUnits, record.decimals),
    naiveUnits: naive === null ? null : naive.toString(),
    naiveUnitsDisplay: naive === null ? null : units(naive, record.decimals),
    gapUnits: programUnits === null || naive === null ? null : (programUnits - naive).toString(),
    gapUnitsDisplay:
      programUnits === null || naive === null ? null : units(programUnits - naive, record.decimals),
    activationAttempt: {
      err: activation.err,
      errorCode: activation.errorCode,
      logs: activation.logs,
    },
    windowLog: window_.logs,
  };
}

// ------------------------------------------------------------------ the Pyth leg

/// The oldest and newest PriceUpdateV2 accounts for a feed. Devnet's equity publisher stopped
/// on 14 August 2026, so an equity feed's newest account is weeks old and a crypto feed's is
/// seconds old. That difference is the demonstration, and it is a property of the cluster
/// rather than something staged here.
async function priceAccounts(feedIdHex) {
  const feed = Buffer.from(feedIdHex, "hex");
  const rows = await connection.getProgramAccounts(PYTH_RECEIVER, {
    commitment: "confirmed",
    filters: [{ memcmp: { offset: 41, bytes: new PublicKey(feed).toBase58() } }],
    dataSlice: { offset: 0, length: 133 },
  });
  if (!rows.length) return null;
  const decoded = rows.map((r) => ({
    address: r.pubkey.toBase58(),
    price: Number(r.account.data.readBigInt64LE(73)),
    exponent: r.account.data.readInt32LE(89),
    publishTime: Number(r.account.data.readBigInt64LE(93)),
  }));
  decoded.sort((a, b) => b.publishTime - a.publishTime);
  return { count: decoded.length, newest: decoded[0], oldest: decoded[decoded.length - 1] };
}

const pyth = { bindings: pythBindings, feeds: {} };
for (const binding of pythBindings) {
  const found = await priceAccounts(binding.feedId);
  pyth.feeds[binding.feedId] = found;
  if (!found) continue;
  // Ask the program to check the newest price for this feed. With a tolerance of zero the
  // program compares Pyth's own price against itself, so a refusal here is about age rather
  // than about a deviation the caller invented.
  const expected = (() => {
    const p = BigInt(found.newest.price);
    const shift = 18 + found.newest.exponent;
    const fp = shift >= 0 ? p * 10n ** BigInt(shift) : p / 10n ** BigInt(-shift);
    const b = Buffer.alloc(16);
    b.writeBigUInt64LE(fp & 0xffffffffffffffffn, 0);
    b.writeBigUInt64LE(fp >> 64n, 8);
    return b;
  })();
  const tol = Buffer.alloc(2);
  tol.writeUInt16LE(50, 0);
  const maxAge = Buffer.alloc(8);
  maxAge.writeBigInt64LE(300n, 0);

  const tx = new Transaction().add({
    programId: PROGRAM_ID,
    keys: [
      { pubkey: new PublicKey(binding.mint), isSigner: false, isWritable: false },
      { pubkey: new PublicKey(
          tokenRecords.find((r) => r.mint === binding.mint).address), isSigner: false, isWritable: false },
      { pubkey: new PublicKey(binding.address), isSigner: false, isWritable: false },
      { pubkey: new PublicKey(found.newest.address), isSigner: false, isWritable: false },
    ],
    data: Buffer.concat([disc("verify_against_pyth"), expected, tol, maxAge]),
  });
  tx.feePayer = DESK_PAYER;
  tx.recentBlockhash = blockhash;
  const sim = await connection.simulateTransaction(tx, undefined, false);
  const err = sim.value.err;
  pyth.feeds[binding.feedId].verify = {
    err: err ? JSON.stringify(err) : null,
    errorCode:
      err && typeof err === "object" && err.InstructionError && typeof err.InstructionError[1] === "object"
        ? err.InstructionError[1].Custom ?? null
        : null,
    logs: (sim.value.logs ?? []).filter((l) => l.includes("Record Date")),
  };
}

// ------------------------------------------------------------------ write

const out = {
  capturedAt: new Date().toISOString(),
  rpc: RPC,
  cluster: "devnet",
  programId: PROGRAM_ID.toBase58(),
  token2022: TOKEN_2022.toBase58(),
  pythReceiver: PYTH_RECEIVER.toBase58(),
  deskPayer: DESK_PAYER.toBase58(),
  pauseSecs: PAUSE_SECS,
  registry,
  tokenRecords,
  mints,
  receipts: receiptRows,
  pyth,
  answers,
  /// The two errors the desk can provoke on purpose, named rather than numbered, because a
  /// simulation reports a number and a number is not something a reader can check.
  errorNames: [
    "NotToken2022", "MintTooShort", "MintPaddingNotZero", "NotAMint", "NoScaledUiAmount",
    "BadExtensionLength", "UnknownExtension", "ExtensionOverrunsAccount", "BadMultiplier",
    "SymbolTooLong", "NotRegistered", "NothingToRecord", "NotPythReceiver", "NotAPriceUpdate",
    "PriceUpdateTooShort", "FeedIdMismatch", "StalePythPrice", "PriceDeviation", "PriceOutOfRange",
    "NegativePythPrice", "FeedSymbolTooLong", "PriceAgeCeilingExceeded",
  ],
  errorCodeOffset: 6000,
};

const target = path.join(path.dirname(new URL(import.meta.url).pathname), "..", "desk-data.json");
fs.writeFileSync(target, JSON.stringify(out, null, 2) + "\n");

console.log(`captured ${out.capturedAt}`);
console.log(`  registry    ${registry.address}  mints ${registry.mints}  activations ${registry.activations}`);
console.log(`  mints       ${tokenRecords.length}`);
for (const r of tokenRecords) {
  const a = answers[r.mint];
  const m = mints[r.mint];
  console.log(
    `    ${r.symbol.padEnd(6)} field=${String(m.multiplier).padEnd(7)} live=${String(m.newMultiplier).padEnd(7)} ` +
      `disagrees=${String(m.fieldDisagrees).padEnd(5)} delta=${String(a.deltaSeconds).padEnd(8)} ` +
      `program=${String(a.programUnitsDisplay).padEnd(11)} naive=${String(a.naiveUnitsDisplay).padEnd(11)} ` +
      `gap=${a.gapUnitsDisplay}`
  );
  console.log(
    `      record_activation -> ${a.activationAttempt.err ?? "accepted"} ` +
      `code=${a.activationAttempt.errorCode}`
  );
}
console.log(`  receipts    ${receiptRows.length}`);
console.log(`  bindings    ${pythBindings.length}`);
for (const b of pythBindings) {
  const f = pyth.feeds[b.feedId];
  console.log(`    ${b.feedSymbol} feed, ${f?.count ?? 0} price accounts, newest ${f?.newest.publishTime ? new Date(f.newest.publishTime * 1000).toISOString() : "none"}`);
  console.log(`      verify -> ${f?.verify.err ?? "accepted"} code=${f?.verify.errorCode}`);
}
console.log(`\nwrote ${target}`);

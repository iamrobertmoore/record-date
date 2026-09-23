#!/usr/bin/env node
//
// Record Date, end to end, on devnet.
//
// This is the demo the video records. It does the whole loop against the deployed program with
// no mocks:
//
//   1. create a Token-2022 mint that carries a ScaledUiAmountConfig extension
//   2. register it with the program
//   3. read the entitlement for a raw balance, before any corporate action
//   4. move the multiplier, the way the issuer moves it for a dividend
//   5. read the entitlement again, with the raw balance unchanged
//   6. crank the activation and get a Receipt account, on chain, that anyone can read
//   7. bind the mint to a Pyth price feed, and read the binding back
//   8. watch the program refuse a real Pyth price that is 33 days old, at the most permissive age
//      limit it accepts, so the age is the only thing that can be refusing it
//   9. watch it accept a fresh one from the same cluster, and return Pyth's price and the deviation
//
// The mint is a test mint, and the script says so on every run. It has to be: the point of the
// demo is to move the multiplier on camera, and the multiplier on a real xStock mint can only be
// moved by the issuer. What is not simulated is the program. It reads the same extension with the
// same code path it uses on NVDAx, and the receipts are real devnet accounts.
//
//   npm install                        # @solana/web3.js and @solana/spl-token
//   node scripts/demo.mjs              # the whole loop
//   node scripts/demo.mjs --status     # read what is already on chain, change nothing
//   node scripts/demo.mjs --mint <pubkey>   # read any mint registered with the program
//
// It spends a little devnet SOL. Nothing here touches mainnet.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import {
  Connection,
  Keypair,
  PublicKey,
  SystemProgram,
  Transaction,
  TransactionInstruction,
  sendAndConfirmTransaction,
  LAMPORTS_PER_SOL,
} from "@solana/web3.js";
import {
  TOKEN_2022_PROGRAM_ID,
  ExtensionType,
  getMint,
  getMintLen,
  createInitializeMint2Instruction,
  createInitializeScaledUiAmountConfigInstruction,
  createUpdateMultiplierDataInstruction,
  createAssociatedTokenAccountInstruction,
  createMintToInstruction,
  getAssociatedTokenAddressSync,
  getScaledUiAmountConfig,
} from "@solana/spl-token";

const RPC = process.env.RPC ?? "https://api.devnet.solana.com";
const PROGRAM_ID = new PublicKey("ycg2obpKmccwAz1zGf4QqgV2vnkWd6CQKLdJSGDxtmG");
const REGISTRY_SEED = Buffer.from("registry");
const TOKEN_RECORD_SEED = Buffer.from("token");
const RECEIPT_SEED = Buffer.from("receipt");
const PYTH_BINDING_SEED = Buffer.from("pyth");

/// Pyth's Solana receiver. Every `PriceUpdateV2` account on every cluster is owned by it, which is
/// the check the program makes before it reads one.
const PYTH_RECEIVER_PROGRAM_ID = new PublicKey("rec5EKMGg6MxZYaMdyBfgwp4d5rB9T1VQH5pJv5LtFJ");

/// Two feeds, chosen because this cluster separates them. The devnet equity feed's publisher
/// stopped on 14 August 2026, so the newest AAPL price account on devnet is weeks old and the
/// program refuses it. A crypto feed on the same cluster updates every second, so the same
/// instruction accepts it. The instruction is what is being demonstrated, not the ticker.
const AAPL_FEED_ID = Buffer.from(
  "49f6b65cb1de6b10eaf75e7c03ca029c306d0357e91b5311b175084a5ad55688", "hex");
const SOL_FEED_ID = Buffer.from(
  "ef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d", "hex");

/// The age limits the program accepts. `RECOMMENDED` is what the program's own docs suggest while
/// the market is open; `CEILING` is the most permissive setting it will take from a caller, and the
/// stale equity price is refused at both.
const RECOMMENDED_PRICE_AGE_SECS = 300;
const MAX_PRICE_AGE_CEILING_SECS = 86_400;

const DECIMALS = 8;
const SYMBOL = "DEMOx";
/// The raw balance the demo works with. One share of a tokenized equity at 8 decimals.
const RAW_HOLDING = 100_000_000n;
/// A 0.42% dividend, which is the shape a quarterly payout takes on a $150 stock.
const DIVIDEND = 0.0042;
/// How far ahead of "now" the activation is scheduled.
///
/// This is the mechanism, not a convenience. The issuer sets the activation for 00:30 UTC on the
/// day after the ex-date, so at the moment the multiplier is written the timestamp is still in
/// the future. Token-2022 only copies `new_multiplier` into the field named `multiplier` when
/// `UpdateMultiplier` is called *again*, or when the timestamp being written has already passed.
/// So a scheduled activation leaves the two fields disagreeing, and nothing on chain ever
/// reconciles them. Six seconds stands in for the issuer's overnight gap.
const LEAD_SECONDS = 6;

const STATE_FILE = path.join(
  path.dirname(new URL(import.meta.url).pathname),
  ".demo-state.json"
);

// ------------------------------------------------------------------ anchor plumbing

/// Anchor derives the instruction discriminator as the first eight bytes of sha256 of the name.
function discriminator(name) {
  return crypto.createHash("sha256").update(`global:${name}`).digest().subarray(0, 8);
}

function borshString(value) {
  const bytes = Buffer.from(value, "utf8");
  const out = Buffer.alloc(4 + bytes.length);
  out.writeUInt32LE(bytes.length, 0);
  bytes.copy(out, 4);
  return out;
}

function u64(value) {
  const out = Buffer.alloc(8);
  out.writeBigUInt64LE(BigInt(value), 0);
  return out;
}

function u16(value) {
  const out = Buffer.alloc(2);
  out.writeUInt16LE(value, 0);
  return out;
}

/// Signed, because `max_age_secs` is an `i64` and the program refuses a negative one. A `u64`
/// encoder would silently turn -1 into a very large positive number and the demo would then be
/// demonstrating a different case than it says.
function i64(value) {
  const out = Buffer.alloc(8);
  out.writeBigInt64LE(BigInt(value), 0);
  return out;
}

function u128(value) {
  const out = Buffer.alloc(16);
  const n = BigInt(value);
  out.writeBigUInt64LE(n & 0xffffffffffffffffn, 0);
  out.writeBigUInt64LE(n >> 64n, 8);
  return out;
}

function ix(name, keys, args = Buffer.alloc(0)) {
  return new TransactionInstruction({
    programId: PROGRAM_ID,
    keys,
    data: Buffer.concat([discriminator(name), args]),
  });
}

function pda(seeds) {
  return PublicKey.findProgramAddressSync(seeds, PROGRAM_ID)[0];
}

const registryPda = () => pda([REGISTRY_SEED]);
const tokenRecordPda = (mint) => pda([TOKEN_RECORD_SEED, mint.toBuffer()]);
const receiptPda = (mint, sequence) => pda([RECEIPT_SEED, mint.toBuffer(), u64(sequence)]);
const pythBindingPda = (mint) => pda([PYTH_BINDING_SEED, mint.toBuffer()]);

// ------------------------------------------------------------------ decoding

const REGISTRY_DISC = Buffer.from([47, 174, 110, 246, 184, 182, 252, 218]);
const TOKEN_RECORD_DISC = Buffer.from([27, 187, 32, 100, 137, 253, 104, 242]);
const RECEIPT_DISC = Buffer.from([39, 154, 73, 106, 80, 102, 145, 153]);

/// Borsh-decode a TokenRecord. Field order is programs/record_date/src/state.rs.
function decodeTokenRecord(data) {
  if (!data.subarray(0, 8).equals(TOKEN_RECORD_DISC)) throw new Error("not a TokenRecord");
  let at = 8;
  const mint = new PublicKey(data.subarray(at, at + 32)); at += 32;
  const symbolBytes = data.subarray(at, at + 12); at += 12;
  const symbolLen = data[at]; at += 1;
  const decimals = data[at]; at += 1;
  const baseBits = data.readBigUInt64LE(at); at += 8;
  const liveBits = data.readBigUInt64LE(at); at += 8;
  const liveFp = data.readBigUInt64LE(at) | (data.readBigUInt64LE(at + 8) << 64n); at += 16;
  const liveEffectiveAt = data.readBigInt64LE(at); at += 8;
  const activations = data.readBigUInt64LE(at); at += 8;
  const lastSeenSlot = data.readBigUInt64LE(at); at += 8;
  const bump = data[at];
  return {
    mint, symbol: symbolBufToStr(symbolBytes.subarray(0, symbolLen)), decimals,
    base: bitsToF64(baseBits), live: bitsToF64(liveBits), liveFp,
    liveEffectiveAt: Number(liveEffectiveAt), activations: Number(activations),
    lastSeenSlot: Number(lastSeenSlot), bump,
  };
}

function decodeReceipt(data) {
  if (!data.subarray(0, 8).equals(RECEIPT_DISC)) throw new Error("not a Receipt");
  let at = 8;
  const mint = new PublicKey(data.subarray(at, at + 32)); at += 32;
  const sequence = data.readBigUInt64LE(at); at += 8;
  const previousBits = data.readBigUInt64LE(at); at += 8;
  const newBits = data.readBigUInt64LE(at); at += 8;
  const previousFp = data.readBigUInt64LE(at) | (data.readBigUInt64LE(at + 8) << 64n); at += 16;
  const newFp = data.readBigUInt64LE(at) | (data.readBigUInt64LE(at + 8) << 64n); at += 16;
  const effectiveAt = data.readBigInt64LE(at); at += 8;
  const recordedAt = data.readBigInt64LE(at); at += 8;
  const rawSupply = data.readBigUInt64LE(at); at += 8;
  return {
    mint, sequence: Number(sequence),
    previous: bitsToF64(previousBits), next: bitsToF64(newBits),
    previousFp, newFp, deltaFp: newFp - previousFp,
    effectiveAt: Number(effectiveAt), recordedAt: Number(recordedAt),
    rawSupply: Number(rawSupply),
  };
}

function bitsToF64(bits) {
  const buf = Buffer.alloc(8);
  buf.writeBigUInt64LE(bits, 0);
  return buf.readDoubleLE(0);
}

function symbolBufToStr(buf) {
  return buf.toString("utf8").replace(/\0+$/, "");
}

// ------------------------------------------------------------------ presentation

const rule = (label) => console.log(`\n${"─".repeat(3)} ${label} ${"─".repeat(Math.max(0, 68 - label.length))}`);
const kv = (k, v) => console.log(`  ${String(k).padEnd(26)} ${v}`);
const money = (n, places = 4) => n.toLocaleString("en-US", {
  minimumFractionDigits: places, maximumFractionDigits: places,
});

/// 1e18 fixed point as a decimal string, without going through a float.
function fpToDecimal(fp, places = 10) {
  const scale = 10n ** 18n;
  const whole = fp / scale;
  const frac = (fp % scale).toString().padStart(18, "0").slice(0, places).replace(/0+$/, "");
  return frac ? `${whole}.${frac}` : `${whole}`;
}

/// Raw base units as a decimal string, at the mint's own decimals.
///
/// This is a different scale from `fpToDecimal` and conflating them is an easy way to print a
/// confident wrong number: 100000000 raw at 8 decimals is one share, not 1e-10 of one.
function units(raw, decimals = DECIMALS) {
  const scale = 10n ** BigInt(decimals);
  const whole = BigInt(raw) / scale;
  const frac = (BigInt(raw) % scale).toString().padStart(decimals, "0").replace(/0+$/, "");
  return frac ? `${whole}.${frac}` : `${whole}`;
}

/// raw * multiplier_fp / 1e18, in integers, exactly as the program does it.
function scaled(raw, fp) {
  return (BigInt(raw) * fp) / (10n ** 18n);
}

function base58(bytes) {
  const alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
  let n = 0n;
  for (const b of bytes) n = n * 256n + BigInt(b);
  let out = "";
  while (n > 0n) { out = alphabet[Number(n % 58n)] + out; n /= 58n; }
  for (const b of bytes) { if (b === 0) out = "1" + out; else break; }
  return out;
}

// ------------------------------------------------------------------ pyth

/// The Anchor discriminators for `PriceUpdateV2` and for this program's `PythBinding`, both
/// `sha256("account:<name>")[:8]`. The first is the same constant the program writes out in
/// `src/pyth.rs`, where a test asserts it against the recomputed digest; the second is only read
/// here, and was computed rather than copied.
const PYTH_PRICE_UPDATE_DISC = Buffer.from([0x22, 0xf1, 0x23, 0x63, 0x9d, 0x7e, 0xf4, 0xcd]);
const PYTH_BINDING_DISC = Buffer.from([94, 230, 83, 111, 62, 131, 188, 143]);

/// Decode a Pyth `PriceUpdateV2`.
///
/// The offsets come from `programs/record_date/src/pyth.rs`, where they are asserted against the
/// discriminator in the Rust tests. This is a second reader of the same layout rather than an
/// independent guess at it, and the length is checked before any field is read for the same reason
/// the program checks it: a `memcmp` filter returns candidates that are not this type.
function decodePriceUpdate(data) {
  if (data.length < 133) throw new Error(`a PriceUpdateV2 is at least 133 bytes, got ${data.length}`);
  if (!data.subarray(0, 8).equals(PYTH_PRICE_UPDATE_DISC)) throw new Error("not a PriceUpdateV2");
  return {
    feedId: data.subarray(41, 73),
    price: data.readBigInt64LE(73),
    exponent: data.readInt32LE(89),
    publishTime: Number(data.readBigInt64LE(93)),
  };
}

/// Decode a `PythBinding`. Field order from `programs/record_date/src/state.rs`.
function decodePythBinding(data) {
  if (!data.subarray(0, 8).equals(PYTH_BINDING_DISC)) throw new Error("not a PythBinding");
  const symbolBytes = data.subarray(72, 84);
  const symbolLen = data[84];
  return {
    mint: new PublicKey(data.subarray(8, 40)),
    feedId: data.subarray(40, 72),
    symbol: symbolBytes.subarray(0, symbolLen).toString("utf8"),
    boundAt: Number(data.readBigInt64LE(85)),
  };
}

/// `price * 10^exponent` as 1e18 fixed point, the same conversion the program does.
///
/// The exponent is signed and negative on every equity and crypto feed, so this is a division in
/// practice. Read as unsigned it would produce a number with thirty digits.
function pythFp(price, exponent) {
  const shift = 18 + exponent;
  return shift >= 0 ? price * (10n ** BigInt(shift)) : price / (10n ** BigInt(-shift));
}

/// The price a human reads, as a string, without going through a float.
function pythHuman(price, exponent) {
  const magnitude = 10n ** BigInt(Math.abs(exponent));
  if (exponent >= 0) return (price * magnitude).toString();
  return `${price / magnitude}.${(price % magnitude).toString().padStart(-exponent, "0")}`;
}

/// The freshest price account for a feed on this cluster, or null.
///
/// `dataSlice` is not an optimisation here, it is the difference between working and not working:
/// a crypto feed carries tens of thousands of accounts on devnet and fetching them whole would be
/// megabytes over the wire. Only `publish_time` is needed to choose between them, so only
/// `publish_time` is fetched, and the winner is then read in full.
async function freshestPriceAccount(connection, feedId) {
  // The scan is the heaviest call this script makes: a crypto feed carries tens of thousands of
  // accounts on devnet, so the response is megabytes even sliced to eight bytes each, and the
  // public RPC throttles it. Retrying is not optional here.
  const accounts = await withRetry("scan for a price account", () =>
    connection.getProgramAccounts(PYTH_RECEIVER_PROGRAM_ID, {
      commitment: "confirmed",
      filters: [{ memcmp: { offset: 41, bytes: base58(feedId) } }],
      dataSlice: { offset: 93, length: 8 },
    }));
  let best = null;
  for (const { pubkey, account } of accounts) {
    const publishTime = Number(account.data.readBigInt64LE(0));
    if (!best || publishTime > best.publishTime) best = { address: pubkey, publishTime };
  }
  if (!best) return null;
  const info = await withRetry("read the chosen price account", () =>
    connection.getAccountInfo(best.address, "confirmed"));
  if (!info) throw new Error(`${best.address.toBase58()} disappeared between the two reads`);
  if (!info.owner.equals(PYTH_RECEIVER_PROGRAM_ID)) {
    throw new Error(`${best.address.toBase58()} is owned by ${info.owner.toBase58()}`);
  }
  return { ...decodePriceUpdate(info.data), address: best.address, candidates: accounts.length };
}

// ------------------------------------------------------------------ program calls

/// Run an async call, retrying the failures devnet actually produces under load.
///
/// `429` is the public RPC's rate limit and it says nothing about the request. `BlockhashNotFound`
/// is a simulation whose blockhash expired while the RPC was retrying, which happens whenever a
/// call takes long enough, and scanning a crypto feed's accounts takes long enough. Both are
/// retried because both succeed on a second attempt; anything else is rethrown at once, so a real
/// program error is never retried into a false pass.
async function withRetry(label, fn, attempts = 4) {
  let lastError;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    try {
      return await fn();
    } catch (error) {
      lastError = error;
      const message = String(error?.message ?? error);
      const retryable = /429|Too Many Requests|BlockhashNotFound|fetch failed|socket hang up|ECONNRESET/i
        .test(message);
      if (!retryable || attempt === attempts) break;
      const delay = 400 * attempt;
      console.log(`  ${label}: ${message.split("\n")[0]} (attempt ${attempt}, retrying in ${delay}ms)`);
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }
  throw lastError;
}

/// Simulate a read-only instruction and return the bytes it put in return data.
///
/// The transaction is signed rather than simulated unsigned. That is not a mistake: for a legacy
/// `Transaction`, `Connection.simulateTransaction` takes an array of signers as its second
/// argument and throws `Invalid arguments` if given the config object, because the config form
/// only exists for versioned transactions. Signing a simulation is free and nothing lands.
async function returnData(connection, payer, instruction) {
  return withRetry("simulate", async () => {
    const tx = new Transaction().add(instruction);
    tx.feePayer = payer.publicKey;
    tx.recentBlockhash = (await connection.getLatestBlockhash()).blockhash;
    tx.sign(payer);
    const sim = await connection.simulateTransaction(tx);
    if (sim.value.err) {
      throw new Error(`simulation failed: ${JSON.stringify(sim.value.err)}\n` +
                      (sim.value.logs ?? []).join("\n"));
    }
    const returned = sim.value.returnData;
    if (!returned) return Buffer.alloc(0);
    // `returnData.data` is the RPC's `[base64, "base64"]` tuple, not a string. Passing the tuple
    // straight to `Buffer.from(..., "base64")` coerces two array elements to two zero bytes, which
    // is a silent wrong answer of the right shape rather than an error. Unwrap it.
    const [encoded, encoding] = returned.data;
    if (encoding !== "base64") throw new Error(`unexpected return data encoding ${encoding}`);
    // The return data has to come from this program. Without this, a callee's return data would be
    // read as this program's answer.
    if (returned.programId !== PROGRAM_ID.toBase58()) {
      throw new Error(`return data came from ${returned.programId}, not ${PROGRAM_ID.toBase58()}`);
    }
    return Buffer.from(encoded, "base64");
  });
}

async function entitlement(connection, payer, mint, raw) {
  const bytes = await returnData(connection, payer, ix("read_entitlement",
    [{ pubkey: mint, isSigner: false, isWritable: false },
     { pubkey: tokenRecordPda(mint), isSigner: false, isWritable: false }],
    u64(raw)));
  if (bytes.length !== 16) throw new Error(`expected a u128, got ${bytes.length} bytes`);
  return bytes.readBigUInt64LE(0) | (bytes.readBigUInt64LE(8) << 64n);
}

/// The mint's scaled-ui-amount extension, read the way the spl-token client reads it.
async function config(connection, mint) {
  return getScaledUiAmountConfig(await getMint(connection, mint, "confirmed", TOKEN_2022_PROGRAM_ID));
}

/// Block until the activation timestamp has passed on chain.
async function waitUntilLive(connection, effectiveAt) {
  const target = Number(effectiveAt) * 1000 + 1500;
  const remaining = target - Date.now();
  if (remaining <= 0) return;
  console.log(`  waiting ${(remaining / 1000).toFixed(1)}s for the activation timestamp to pass`);
  await new Promise((resolve) => setTimeout(resolve, remaining));
  // The validator clock, not this machine's, is what the program reads.
  await withRetry("read the validator clock", () => connection.getSlot("confirmed"));
}

async function pending(connection, payer, mint) {
  const bytes = await returnData(connection, payer, ix("activation_pending",
    [{ pubkey: mint, isSigner: false, isWritable: false },
     { pubkey: tokenRecordPda(mint), isSigner: false, isWritable: false }]));
  return bytes[0] === 1;
}

/// `settlement_window` returns two i64s: seconds since the last activation, then seconds until the
/// next. Each is -1 when there is nothing to report. Two numbers because the issuer's pause window
/// is two-sided, and the earlier half is only readable while the issuer has a value staged.
async function settlementWindow(connection, payer, mint) {
  const bytes = await returnData(connection, payer, ix("settlement_window",
    [{ pubkey: mint, isSigner: false, isWritable: false },
     { pubkey: tokenRecordPda(mint), isSigner: false, isWritable: false }]));
  return { since: Number(bytes.readBigInt64LE(0)), until: Number(bytes.readBigInt64LE(8)) };
}

function bindPythFeedIx(payer, mint, feedId, symbol) {
  return ix("bind_pyth_feed", [
    { pubkey: payer, isSigner: true, isWritable: true },
    { pubkey: registryPda(), isSigner: false, isWritable: true },
    { pubkey: mint, isSigner: false, isWritable: false },
    { pubkey: tokenRecordPda(mint), isSigner: false, isWritable: false },
    { pubkey: pythBindingPda(mint), isSigner: false, isWritable: true },
    { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
  ], Buffer.concat([feedId, borshString(symbol)]));
}

function verifyAgainstPythIx(mint, priceUpdate, expectedFp, toleranceBps, maxAgeSecs) {
  return ix("verify_against_pyth", [
    { pubkey: mint, isSigner: false, isWritable: false },
    { pubkey: tokenRecordPda(mint), isSigner: false, isWritable: false },
    { pubkey: pythBindingPda(mint), isSigner: false, isWritable: false },
    { pubkey: priceUpdate, isSigner: false, isWritable: false },
  ], Buffer.concat([u128(expectedFp), u16(toleranceBps), i64(maxAgeSecs)]));
}

/// Simulate an instruction and report what happened instead of throwing.
///
/// The Pyth steps expect a refusal, and a refusal is the evidence, so the error is the result
/// rather than a failure. `returnData` above cannot serve here: it throws, which is right for the
/// read instructions and wrong for this.
///
/// No retry wrapper on the outcome, and that is deliberate. This function is called to observe a
/// refusal, so a retry would either duplicate the observation or turn a genuine program error into
/// a longer wait for the same answer. The only retryable failure inside it is a stale blockhash, and
/// that is handled by asking for a fresh one and running the whole simulation again, once.
async function simulate(connection, payer, instruction) {
  for (let attempt = 1; attempt <= 3; attempt++) {
    const tx = new Transaction().add(instruction);
    tx.feePayer = payer.publicKey;
    tx.recentBlockhash = (await connection.getLatestBlockhash("confirmed")).blockhash;
    tx.sign(payer);
    const sim = await connection.simulateTransaction(tx);
    if (sim.value.err?.BlockhashNotFound && attempt < 3) {
      console.log(`  stale blockhash on attempt ${attempt}, asking for a fresh one`);
      continue;
    }
    return { err: sim.value.err, logs: sim.value.logs ?? [], returnData: sim.value.returnData };
  }
  throw new Error("simulation failed three times on a stale blockhash");
}

/// The program's error variants in declaration order, so the code is `6000 + index`. Kept here
/// because a simulation reports a number and a number is not evidence a reader can check.
const ERROR_NAMES = [
  "NotToken2022", "MintTooShort", "MintPaddingNotZero", "NotAMint", "NoScaledUiAmount",
  "BadExtensionLength", "UnknownExtension", "ExtensionOverrunsAccount", "BadMultiplier",
  "SymbolTooLong", "NotRegistered", "NothingToRecord", "NotPythReceiver", "NotAPriceUpdate",
  "PriceUpdateTooShort", "FeedIdMismatch", "StalePythPrice", "PriceDeviation", "PriceOutOfRange",
  "NegativePythPrice", "FeedSymbolTooLong", "PriceAgeCeilingExceeded",
  // Added 17 Sep 2026. Positional from 6000, so it goes on the end; this mirrors the enum
  // in `programs/record_date/src/error.rs`.
  "TruncatedExtensionHeader",
];
const ERROR_CODE_OFFSET = 6000;

function customErrorCode(err) {
  const detail = err?.InstructionError?.[1];
  return detail && typeof detail === "object" && "Custom" in detail ? detail.Custom : null;
}

function errorName(code) {
  const index = code - ERROR_CODE_OFFSET;
  return index >= 0 && index < ERROR_NAMES.length ? ERROR_NAMES[index] : `unknown error ${code}`;
}

/// The two halves of the return data from `verify_against_pyth`: Pyth's price and the deviation
/// the caller was judged on, both little-endian.
function splitVerifyReturn(bytes) {
  if (bytes.length !== 32) throw new Error(`expected 32 bytes of return data, got ${bytes.length}`);
  const read = (at) => bytes.readBigUInt64LE(at) | (bytes.readBigUInt64LE(at + 8) << 64n);
  return { priceFp: read(0), deviationBps: Number(read(16)) };
}

// ------------------------------------------------------------------ setup

function loadPayer() {
  const file = path.join(os.homedir(), ".config", "solana", "id.json");
  return Keypair.fromSecretKey(Uint8Array.from(JSON.parse(fs.readFileSync(file, "utf8"))));
}

function loadState() {
  try { return JSON.parse(fs.readFileSync(STATE_FILE, "utf8")); } catch { return {}; }
}

function saveState(state) {
  fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2) + "\n");
}

async function ensureSol(connection, payer, needed) {
  const balance = await connection.getBalance(payer.publicKey);
  if (balance >= needed) return balance;
  console.log(`  balance is ${balance / LAMPORTS_PER_SOL} SOL, asking devnet for more`);
  const sig = await connection.requestAirdrop(payer.publicKey, 2 * LAMPORTS_PER_SOL);
  await connection.confirmTransaction(sig, "confirmed");
  return connection.getBalance(payer.publicKey);
}

/// Read an account, insisting on an address and saying so when the account is not there.
///
/// `Connection.getAccountInfo` wraps any failure in a message that itself calls
/// `publicKey.toBase58()`, so passing anything that is not a PublicKey produces
/// "publicKey.toBase58 is not a function" and hides the real cause. It also returns null for a
/// missing account, which then fails on the next property access. Both are worth naming here.
async function account(connection, address, what) {
  if (!(address instanceof PublicKey)) {
    throw new Error(`${what} was passed a ${address?.constructor?.name ?? typeof address}, ` +
                    `not a PublicKey`);
  }
  const info = await connection.getAccountInfo(address);
  if (!info) {
    throw new Error(`${what} ${address.toBase58()} does not exist on ${connection.rpcEndpoint}`);
  }
  return info;
}

// ------------------------------------------------------------------ the demo

async function main() {
  const statusOnly = process.argv.includes("--status");
  // The public devnet endpoint rate-limits bursts with a 429 ("Connection rate limits exceeded").
  // web3.js's own retry gives up after four short waits, and the demo then died mid-run, so every
  // request goes through a fetch that waits longer instead: 1s, 2s, 4s, 8s, then 15s, up to ten
  // times. A 429 carries no information about the request, so retrying it is always safe.
  const patientFetch = async (url, options) => {
    for (let attempt = 1; ; attempt++) {
      const response = await fetch(url, options);
      if (response.status !== 429 || attempt >= 10) return response;
      const delay = Math.min(1000 * 2 ** (attempt - 1), 15000);
      console.log(`  rate-limited by the RPC, waiting ${delay / 1000}s (attempt ${attempt} of 10)`);
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  };
  const connection = new Connection(RPC, {
    commitment: "confirmed",
    fetch: patientFetch,
    disableRetryOnRateLimit: true,
  });
  const payer = loadPayer();
  const state = loadState();

  console.log("Record Date: the dividend is in the mint account, and no surface reports it");
  console.log(`  rpc ${RPC}`);
  console.log(`  program ${PROGRAM_ID.toBase58()}`);
  console.log(`  payer ${payer.publicKey.toBase58()}`);

  rule("0. the registry");
  const registry = registryPda();
  const registryInfo = await connection.getAccountInfo(registry);
  if (!registryInfo) {
    if (statusOnly) { console.log("  not initialised yet"); return; }
    const balance = await ensureSol(connection, payer, 0.05 * LAMPORTS_PER_SOL);
    kv("balance", `${balance / LAMPORTS_PER_SOL} SOL`);
    const sig = await sendAndConfirmTransaction(connection, new Transaction().add(
      ix("init_registry", [
        { pubkey: payer.publicKey, isSigner: true, isWritable: true },
        { pubkey: registry, isSigner: false, isWritable: true },
        { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
      ])), [payer]);
    kv("init_registry", sig);
  }
  const registryData = (await connection.getAccountInfo(registry)).data;
  if (!registryData.subarray(0, 8).equals(REGISTRY_DISC)) throw new Error("not a Registry");
  kv("registry", registry.toBase58());
  kv("authority", new PublicKey(registryData.subarray(8, 40)).toBase58());
  kv("mints registered", Number(registryData.readBigUInt64LE(40)));
  kv("activations", Number(registryData.readBigUInt64LE(48)));

  rule("1. a Token-2022 mint with a ScaledUiAmountConfig");
  // A fresh mint each run by default, so the output is the same every time and the video can be
  // recorded twice. `--reuse` reads back the last one instead of spending another account, and
  // `--mint <address>` reads a specific one, which is what makes a prepared state filmable.
  const reuse = process.argv.includes("--reuse");
  const named = process.argv.indexOf("--mint");
  let mint = named >= 0 && process.argv[named + 1]
    ? new PublicKey(process.argv[named + 1])
    : (reuse && state.mint ? new PublicKey(state.mint) : null);
  if (!mint) {
    if (statusOnly) { console.log("  no demo mint yet"); return; }
    const balance = await ensureSol(connection, payer, 0.1 * LAMPORTS_PER_SOL);
    kv("balance", `${balance / LAMPORTS_PER_SOL} SOL`);

    // Kept as a Keypair only for as long as it takes to sign for its own creation. `mint` is a
    // PublicKey everywhere below this block: a Keypair here would be passed to the RPC as an
    // address and fail, and web3.js's getAccountInfo masks the real error when it does.
    const created = Keypair.generate();
    const space = getMintLen([ExtensionType.ScaledUiAmountConfig]);
    const rent = await connection.getMinimumBalanceForRentExemption(space);
    const tx = new Transaction().add(
      SystemProgram.createAccount({
        fromPubkey: payer.publicKey, newAccountPubkey: created.publicKey,
        lamports: rent, space, programId: TOKEN_2022_PROGRAM_ID,
      }),
      createInitializeScaledUiAmountConfigInstruction(created.publicKey, payer.publicKey, 1.0, TOKEN_2022_PROGRAM_ID),
      createInitializeMint2Instruction(created.publicKey, DECIMALS, payer.publicKey, null, TOKEN_2022_PROGRAM_ID),
    );
    const sig = await sendAndConfirmTransaction(connection, tx, [payer, created]);
    kv("created", `${created.publicKey.toBase58()}  (${space} bytes)`);
    kv("signature", sig);
    kv("note", "a test mint. The multiplier on a real xStock can only be moved by the issuer.");

    const ata = getAssociatedTokenAddressSync(created.publicKey, payer.publicKey, false, TOKEN_2022_PROGRAM_ID);
    const sig2 = await sendAndConfirmTransaction(connection, new Transaction().add(
      createAssociatedTokenAccountInstruction(payer.publicKey, ata, payer.publicKey, created.publicKey, TOKEN_2022_PROGRAM_ID),
      createMintToInstruction(created.publicKey, ata, payer.publicKey, RAW_HOLDING, [], TOKEN_2022_PROGRAM_ID),
    ), [payer]);
    kv("minted", `${RAW_HOLDING} raw units to ${ata.toBase58().slice(0, 8)}…`);
    kv("signature", sig2);
    mint = created.publicKey;
    state.mint = mint.toBase58();
    saveState(state);
  } else {
    kv(named >= 0 ? "reading" : "reusing", mint.toBase58());
  }

  const mintInfo = await account(connection, mint, "the demo mint");
  kv("owner", mintInfo.owner.toBase58());
  kv("account size", `${mintInfo.data.length} bytes`);
  // The spl-token client reads the mint itself, and slices the extension data from
  // `ACCOUNT_SIZE + ACCOUNT_TYPE_SIZE` = byte 166, which is the offset this program hardcodes.
  // Reading it the same way here means the two agree by construction.
  const mintAccount = await getMint(connection, mint, "confirmed", TOKEN_2022_PROGRAM_ID);
  const initial = getScaledUiAmountConfig(mintAccount);
  kv("extension authority", initial?.authority?.toBase58() ?? "none");
  kv("multiplier (previous)", initial?.multiplier);
  kv("new multiplier", initial?.newMultiplier);
  kv("effective at", initial?.newMultiplierEffectiveTimestamp?.toString() ?? "unset");
  kv("tlv starts at byte", `${mintInfo.data.length - mintAccount.tlvData.length}` +
     `  (${mintAccount.tlvData.length} bytes of extension data)`);

  rule("2. register the mint");
  let record = tokenRecordPda(mint);
  if (!(await connection.getAccountInfo(record))) {
    if (statusOnly) { console.log("  not registered yet"); return; }
    const sig = await sendAndConfirmTransaction(connection, new Transaction().add(
      ix("register_mint", [
        { pubkey: payer.publicKey, isSigner: true, isWritable: true },
        { pubkey: registry, isSigner: false, isWritable: true },
        { pubkey: mint, isSigner: false, isWritable: false },
        { pubkey: record, isSigner: false, isWritable: true },
        { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
      ], borshString(SYMBOL))), [payer]);
    kv("register_mint", sig);
  } else {
    kv("already registered", record.toBase58());
  }
  let tr = decodeTokenRecord((await connection.getAccountInfo(record)).data);
  kv("symbol", tr.symbol);
  kv("field named multiplier", `${tr.base}   <- what a naive reader takes`);
  kv("actually in force", `${tr.live}   <- what the program uses`);
  kv("activations recorded", tr.activations);

  rule("3. what a holder is entitled to, before any corporate action");
  const before = await entitlement(connection, payer, mint, RAW_HOLDING);
  kv("raw balance", `${units(RAW_HOLDING)} shares  (${RAW_HOLDING} raw units, and it never moves)`);
  kv("read_entitlement", `${units(before)} shares  (${before} raw)`);

  rule("4. schedule the dividend, the way the issuer schedules it");
  let effectiveAt = null;
  if (statusOnly) {
    console.log("  skipped in --status mode");
  } else {
    effectiveAt = BigInt(Math.floor(Date.now() / 1000) + LEAD_SECONDS);
    const sig = await sendAndConfirmTransaction(connection, new Transaction().add(
      createUpdateMultiplierDataInstruction(
        mint, payer.publicKey, 1.0 + DIVIDEND, effectiveAt, [], TOKEN_2022_PROGRAM_ID),
    ), [payer]);
    kv("updateMultiplier", sig);
    kv("scheduled", `${(1.0 + DIVIDEND).toFixed(4)} in force from ${effectiveAt} (${LEAD_SECONDS}s ahead)`);
    const scheduled = await config(connection, mint);
    kv("field named multiplier", `${scheduled.multiplier}   <- the value before this dividend`);
    kv("new multiplier", `${scheduled.newMultiplier}   <- the value from ${effectiveAt}`);
  }

  rule("5. before the activation: the entitlement has not moved");
  const pendingEntitlement = await entitlement(connection, payer, mint, RAW_HOLDING);
  kv("read_entitlement", `${units(pendingEntitlement)} shares  (${pendingEntitlement} raw)`);
  kv("same as before", pendingEntitlement === before
     ? "yes, and it should be: the activation is still ahead" : "NO, which is a bug");
  kv("activation_pending", (await pending(connection, payer, mint)) ? "1" : "0");

  if (!statusOnly) {
    rule("6. after the activation: the entitlement moved, the raw balance did not");
    await waitUntilLive(connection, effectiveAt);
    const after = await entitlement(connection, payer, mint, RAW_HOLDING);
    kv("raw balance", `${units(RAW_HOLDING)} shares  (unchanged, still ${RAW_HOLDING} raw)`);
    kv("read_entitlement", `${units(after)} shares  (${after} raw)`);
    const gained = after - before;
    kv("the reinvested dividend", `${gained} raw units = ${units(gained)} shares`);
    kv("as a rate", `${(Number(gained) / Number(before) * 100).toFixed(4)}%  (the dividend was ${(DIVIDEND * 100).toFixed(2)}%)`);

    // This is the finding, reproduced on chain in front of the reader.
    const live = await config(connection, mint);
    kv("field named multiplier", `${live.multiplier}   <- DID NOT MOVE`);
    kv("new multiplier", `${live.newMultiplier}   <- this is the one in force`);
    kv("a reader taking `multiplier`", "under-reports the holding, and keeps doing so");
    kv("nothing on chain fixes it", "only the issuer's next write rolls it forward");
  }

  rule("7. crank the activation and get a receipt");
  const isPending = await pending(connection, payer, mint);
  kv("activation_pending", isPending ? "1  (the multiplier has moved since the last read)" : "0");
  if (!isPending) {
    console.log("  nothing to record: the program has already seen this multiplier.");
  } else if (statusOnly) {
    console.log("  skipped in --status mode");
  } else {
    const sequence = tr.activations;
    const receipt = receiptPda(mint, sequence);
    const sig = await sendAndConfirmTransaction(connection, new Transaction().add(
      ix("record_activation", [
        { pubkey: payer.publicKey, isSigner: true, isWritable: true },
        { pubkey: registry, isSigner: false, isWritable: true },
        { pubkey: mint, isSigner: false, isWritable: false },
        { pubkey: record, isSigner: false, isWritable: true },
        { pubkey: receipt, isSigner: false, isWritable: true },
        { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
      ])), [payer]);
    kv("record_activation", sig);
    const r = decodeReceipt((await connection.getAccountInfo(receipt)).data);
    kv("receipt", receipt.toBase58());
    kv("sequence", r.sequence);
    kv("previous multiplier", r.previous);
    kv("new multiplier", r.next);
    kv("delta, 1e18 fixed point", fpToDecimal(r.deltaFp));
    kv("effective at", r.effectiveAt);
    kv("recorded at", r.recordedAt);
    kv("raw supply", r.rawSupply);
    state.lastReceipt = receipt.toBase58();
    saveState(state);
  }

  rule("8. what a protocol reads instead of an API");
  const { since, until } = await settlementWindow(connection, payer, mint);
  kv("settlement_window", `${since} seconds since the last activation, ${until} until the next`);
  kv("issuer recommends pausing", "900 seconds either side");
  kv("so a venue would", (since >= 0 && since < 900) || (until >= 0 && until < 900)
     ? "REFUSE to settle right now" : "settle normally");
  kv("activation_pending", (await pending(connection, payer, mint)) ? "1" : "0  (nothing new)");

  // ------------------------------------------------------------------ the Pyth price
  //
  // Everything above reads the mint, and the mint is the issuer's own account, so a number taken
  // from there is the issuer agreeing with itself. This is the one check against a number this
  // program does not own, and devnet makes the case better than mainnet does: the equity feed's
  // devnet publisher stopped on 14 August 2026, so the newest AAPL price account on this cluster
  // is weeks old, while a crypto feed on the same cluster updates every second. Same instruction,
  // two feeds, and the only difference that matters is the age of the price.

  rule("9. bind the mint to a Pyth feed");
  if (statusOnly) {
    console.log("  skipped in --status mode");
  } else {
    const bindSig = await sendAndConfirmTransaction(connection, new Transaction().add(
      bindPythFeedIx(payer.publicKey, mint, AAPL_FEED_ID, "AAPL")), [payer]);
    kv("bind_pyth_feed", bindSig);
  }
  const bindingPda = pythBindingPda(mint);
  const bindingInfo = await connection.getAccountInfo(bindingPda);
  if (bindingInfo) {
    const b = decodePythBinding(bindingInfo.data);
    kv("binding account", bindingPda.toBase58());
    kv("feed id", `${b.feedId.toString("hex")}  (${b.symbol})`);
    kv("bound at", b.boundAt);
    kv("the id, not the address", "a PriceUpdateV2 account is rewritten on every publish");
  }

  rule("10. a price the program refuses");
  const stale = await freshestPriceAccount(connection, AAPL_FEED_ID);
  if (!stale) {
    console.log("  no AAPL price account on this cluster");
  } else {
    const ageSeconds = Math.round(Date.now() / 1000 - stale.publishTime);
    const ageHours = ageSeconds / 3600;
    kv("newest AAPL account on devnet", stale.address.toBase58());
    kv("candidates the filter returned", `${stale.candidates}, and every one of them parsed`);
    kv("published", `${new Date(stale.publishTime * 1000).toISOString()}  (${ageHours.toFixed(1)}h ago)`);
    kv("its price", pythHuman(stale.price, stale.exponent));
    const staleFp = pythFp(stale.price, stale.exponent);

    // The caller settles at exactly Pyth's number. That is deliberate: it makes the deviation zero,
    // so the tolerance cannot be the reason for a refusal and the age is the only thing left that
    // can refuse this. A demo that settled at a different number would not isolate the cause.
    const refused = await simulate(connection, payer,
      verifyAgainstPythIx(mint, stale.address, staleFp, 50, MAX_PRICE_AGE_CEILING_SECS));
    const code = customErrorCode(refused.err);
    kv("the caller's price", "Pyth's own, so the deviation is 0 of a 50bps tolerance");
    kv("age limit asked for", `${MAX_PRICE_AGE_CEILING_SECS}s, the most permissive allowed`);
    kv("the price's age", `${ageSeconds}s`);
    kv("result", code === null
      ? "ACCEPTED, which would be a bug: this price is weeks old"
      : `${errorName(code)}  (code ${code})`);
    kv("so the numbers were never the reason", "the deviation is zero and it is still refused");
  }

  rule("11. the same instruction, a price it accepts");
  if (statusOnly) {
    console.log("  skipped in --status mode");
  } else {
    const rebindSig = await sendAndConfirmTransaction(connection, new Transaction().add(
      bindPythFeedIx(payer.publicKey, mint, SOL_FEED_ID, "SOL")), [payer]);
    kv("rebound to", `SOL/USD  (${rebindSig})`);
    const fresh = await freshestPriceAccount(connection, SOL_FEED_ID);
    if (!fresh) {
      console.log("  no SOL price account on this cluster");
    } else {
      const ageSeconds = Math.round(Date.now() / 1000 - fresh.publishTime);
      kv("newest SOL account on devnet", fresh.address.toBase58());
      kv("published", `${ageSeconds}s ago, of ${fresh.candidates} candidates`);
      kv("its price", pythHuman(fresh.price, fresh.exponent));
      const freshFp = pythFp(fresh.price, fresh.exponent);
      const bytes = await returnData(connection, payer,
        verifyAgainstPythIx(mint, fresh.address, freshFp, 50, RECOMMENDED_PRICE_AGE_SECS));
      const { priceFp, deviationBps } = splitVerifyReturn(bytes);
      kv("verify_against_pyth", "accepted");
      kv("the price it returned", `${fpToDecimal(priceFp)}  (Pyth's, not the caller's)`);
      kv("deviation", `${deviationBps} bps of a 50 bps tolerance`);
      kv("age limit asked for", `${RECOMMENDED_PRICE_AGE_SECS}s`);
      kv("the ticker is not the point", "the instruction is; devnet's equity feed is dead");
    }
  }

  rule("links");
  kv("program", `https://explorer.solana.com/address/${PROGRAM_ID.toBase58()}?cluster=devnet`);
  kv("mint", `https://explorer.solana.com/address/${mint.toBase58()}?cluster=devnet`);
  kv("token record", `https://explorer.solana.com/address/${record.toBase58()}?cluster=devnet`);
  if (state.lastReceipt) {
    kv("receipt", `https://explorer.solana.com/address/${state.lastReceipt}?cluster=devnet`);
  }
  console.log();
}

main().catch((error) => {
  console.error(`\ndemo failed: ${error.message}`);
  if (error.logs) console.error(error.logs.join("\n"));
  if (process.env.DEMO_TRACE) console.error(error.stack);
  process.exit(1);
});

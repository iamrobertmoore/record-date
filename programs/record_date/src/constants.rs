use anchor_lang::prelude::*;

/// The Token-2022 program. Every xStock mint is owned by it.
///
/// Written as bytes rather than through a base58 macro so the constant is usable in a `const`
/// context with no runtime parse and no dependency on a macro the anchor re-export does not carry.
pub const TOKEN_2022_PROGRAM_ID: Pubkey = Pubkey::new_from_array([
    6, 221, 246, 225, 238, 117, 143, 222, 24, 66, 93, 188, 228, 108, 205, 218, 182, 26, 252, 77,
    131, 185, 13, 39, 254, 189, 249, 40, 216, 161, 139, 252,
]);

/// Fixed-point scale for the multiplier. The mint stores an f64; everything here is 1e18.
pub const FP_SCALE: u128 = 1_000_000_000_000_000_000;

/// Longest symbol this program will record. Twelve bytes covers every xStock ticker.
pub const SYMBOL_MAX: usize = 12;

/// The pause the issuer recommends around a multiplier activation, in seconds.
/// From docs.xstocks.fi/developers/multipliers: "a brief window (e.g., 15 minutes) before
/// and after each activation timestamp".
pub const RECOMMENDED_PAUSE_SECS: i64 = 15 * 60;

pub const REGISTRY_SEED: &[u8] = b"registry";
pub const TOKEN_RECORD_SEED: &[u8] = b"token";
pub const RECEIPT_SEED: &[u8] = b"receipt";
/// Binds a mint to a Pyth feed. A separate account on purpose: adding a field to `TokenRecord`
/// would change its layout, and every receipt already written stays readable only if the records
/// they were written against keep deserialising.
pub const PYTH_BINDING_SEED: &[u8] = b"pyth";

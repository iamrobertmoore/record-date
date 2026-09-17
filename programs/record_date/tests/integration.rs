//! End to end tests: the compiled program, run in LiteSVM, against a real mint account.
//!
//! `fixtures/nvdax_mint.bin` is the actual 679 byte account of the NVDAx mint
//! (`Xsc9qvGR1efVDFGLrVsmkzv3qi45LTBjeUKSPmx9qEh`), read from Solana mainnet. It is here rather
//! than generated so that the parser is tested against the layout the issuer actually ships:
//! a 165 byte base region, of which the mint fills only the first 82 bytes, then the
//! account-type byte, then the extensions. The first extension header is at byte 166, not at the
//! byte 83 that reading the documented layout would give.
//!
//! These tests need `target/deploy/record_date.so`, so run `anchor build` first.
//!
//!     cargo test --manifest-path programs/record_date/Cargo.toml

use anchor_lang::error::ERROR_CODE_OFFSET;
use anchor_lang::prelude::*;
use anchor_lang::solana_program::clock::Clock;
use anchor_lang::solana_program::instruction::Instruction;
use anchor_lang::solana_program::system_program;
use anchor_lang::{AccountDeserialize, InstructionData};
use litesvm::types::{FailedTransactionMetadata, TransactionMetadata, TransactionResult};
use litesvm::LiteSVM;
use record_date::constants::{
    PYTH_BINDING_SEED, RECEIPT_SEED, RECOMMENDED_PAUSE_SECS, REGISTRY_SEED, TOKEN_2022_PROGRAM_ID,
    TOKEN_RECORD_SEED,
};
use record_date::error::RecordDateError;
use record_date::pyth;
use record_date::state::{PythBinding, Receipt, Registry, TokenRecord};
use record_date::token2022;
use sha2::{Digest, Sha256};
use solana_account::Account;
use solana_instruction_error::InstructionError;
use solana_keypair::Keypair;
use solana_signer::Signer;
use solana_transaction::Transaction;
use solana_transaction_error::TransactionError;

/// The real NVDAx mint, straight off mainnet.
const MINT_BYTES: &[u8] = include_bytes!("fixtures/nvdax_mint.bin");

const MINT: &str = "Xsc9qvGR1efVDFGLrVsmkzv3qi45LTBjeUKSPmx9qEh";
const SYMBOL: &str = "NVDAx";

/// What the account actually holds, confirmed against the issuer's own multiplier endpoint and
/// recorded in fixtures/nvdax_expected.json.
const BASE_MULTIPLIER: f64 = 1.0009180758490996;
const LIVE_MULTIPLIER: f64 = 1.0017011968010741;
const EFFECTIVE_TIMESTAMP: i64 = 1_789_000_200;
const SUPPLY: u64 = 32_127_767_286_397;
const DECIMALS: u8 = 8;
const ACCOUNT_LEN: usize = 679;
/// The header of the ScaledUiAmountConfig extension. Byte 275, not byte 83.
const HEADER_OFFSET: usize = 275;
const EXTENSION_TYPE: u16 = 25;
const EXTENSION_LENGTH: u16 = 56;

/// Comfortably after the activation above, so the new multiplier is the live one.
const NOW: i64 = EFFECTIVE_TIMESTAMP + 6 * 86_400;
const LAMPORTS: u64 = 100_000_000_000;

fn mint_pubkey() -> Pubkey {
    MINT.parse().expect("the mint address parses")
}

fn program_path() -> String {
    format!("{}/../../target/deploy/record_date.so", env!("CARGO_MANIFEST_DIR"))
}

/// The SBPF architecture the ELF was built for, from `e_flags`.
///
/// `anchor build` defaults to `--arch v3`. LiteSVM 0.10.0 cannot load a v3 ELF and reports it as
/// `InvalidAccountData`, which reads like a broken program rather than a build flag. Checking
/// here turns that into an instruction.
fn sbpf_arch(bytes: &[u8]) -> u32 {
    assert!(bytes.len() >= 52, "the program binary is too short to be an ELF");
    assert_eq!(&bytes[..4], b"\x7fELF", "the program binary is not an ELF");
    u32::from_le_bytes(bytes[48..52].try_into().unwrap())
}

/// A fresh VM with the program loaded, a funded payer, a fixed clock, and the real mint.
fn setup() -> (LiteSVM, Keypair, Pubkey) {
    let path = program_path();
    let bytes = std::fs::read(&path).unwrap_or_else(|error| {
        panic!("cannot read {path}: {error}. Run `anchor build --arch v1` first.")
    });
    assert_eq!(
        sbpf_arch(&bytes),
        1,
        "{path} was built for SBPF v{}. LiteSVM 0.10.0 loads v1 only, and reports anything else \
         as InvalidAccountData. Rebuild with `anchor build --arch v1`.",
        sbpf_arch(&bytes)
    );

    let mut svm = LiteSVM::new();
    let payer = Keypair::new();
    svm.airdrop(&payer.pubkey(), LAMPORTS).expect("airdrop");

    svm.add_program_from_file(record_date::ID, &path)
        .expect("the program loads");

    // A fixed clock, so the read rule is tested rather than the wall clock.
    let mut clock: Clock = svm.get_sysvar();
    clock.unix_timestamp = NOW;
    svm.set_sysvar(&clock);

    let mint = mint_pubkey();
    svm.set_account(
        mint,
        Account {
            lamports: 1_000_000_000,
            data: MINT_BYTES.to_vec(),
            owner: TOKEN_2022_PROGRAM_ID,
            executable: false,
            rent_epoch: 0,
        },
    )
    .expect("the mint account is set");

    (svm, payer, mint)
}

/// `anchor_lang::prelude` exports its own `Result`, which is `Result<T, anchor_lang::Error>`, so
/// the return type here has to be LiteSVM's alias rather than a two-argument `Result`.
fn send(svm: &mut LiteSVM, payer: &Keypair, ix: Instruction) -> TransactionResult {
    // LiteSVM keeps a transaction history and rejects a repeat signature as AlreadyProcessed. A
    // read with the same payer and no state change produces a byte-identical transaction, so the
    // second one would be refused. Expiring the blockhash first gives every send a fresh
    // signature, which is what would happen on a real cluster anyway.
    svm.expire_blockhash();
    let blockhash = svm.latest_blockhash();
    let tx = Transaction::new_signed_with_payer(&[ix], Some(&payer.pubkey()), &[payer], blockhash);
    svm.send_transaction(tx)
}

fn send_ok(svm: &mut LiteSVM, payer: &Keypair, ix: Instruction) -> TransactionMetadata {
    match send(svm, payer, ix) {
        Ok(meta) => meta,
        Err(failed) => panic!(
            "expected success, got {:?}\n{}",
            failed.err,
            failed.meta.pretty_logs()
        ),
    }
}

/// The custom error code Anchor returned, or None if the failure was something else.
fn anchor_code(failed: &FailedTransactionMetadata) -> Option<u32> {
    match &failed.err {
        TransactionError::InstructionError(0, InstructionError::Custom(code)) => Some(*code),
        _ => None,
    }
}

fn expect_anchor_error(
    svm: &mut LiteSVM,
    payer: &Keypair,
    ix: Instruction,
    error: RecordDateError,
) {
    let failed = send(svm, payer, ix).expect_err("expected this instruction to fail");
    let want = ERROR_CODE_OFFSET + error as u32;
    assert_eq!(
        anchor_code(&failed),
        Some(want),
        "expected {:?} (code {}), got {:?}\n{}",
        error,
        want,
        failed.err,
        failed.meta.pretty_logs()
    );
}

fn registry_pda() -> (Pubkey, u8) {
    Pubkey::find_program_address(&[REGISTRY_SEED], &record_date::ID)
}

fn token_record_pda(mint: &Pubkey) -> (Pubkey, u8) {
    Pubkey::find_program_address(&[TOKEN_RECORD_SEED, mint.as_ref()], &record_date::ID)
}

fn receipt_pda(mint: &Pubkey, sequence: u64) -> (Pubkey, u8) {
    Pubkey::find_program_address(
        &[RECEIPT_SEED, mint.as_ref(), &sequence.to_le_bytes()],
        &record_date::ID,
    )
}

// ---------------------------------------------------------------- instruction builders
//
// Anchor 1.2 no longer generates a client-accounts struct, so the metas are written out. The
// order has to match the field order of the accounts struct in the program, which makes these
// four functions a readable statement of the interface.

fn ix_init_registry(payer: &Pubkey) -> Instruction {
    Instruction {
        program_id: record_date::ID,
        accounts: vec![
            AccountMeta::new(*payer, true),
            AccountMeta::new(registry_pda().0, false),
            AccountMeta::new_readonly(system_program::ID, false),
        ],
        data: record_date::instruction::InitRegistry {}.data(),
    }
}

fn ix_register_mint(payer: &Pubkey, mint: &Pubkey) -> Instruction {
    Instruction {
        program_id: record_date::ID,
        accounts: vec![
            AccountMeta::new(*payer, true),
            AccountMeta::new(registry_pda().0, false),
            AccountMeta::new_readonly(*mint, false),
            AccountMeta::new(token_record_pda(mint).0, false),
            AccountMeta::new_readonly(system_program::ID, false),
        ],
        data: record_date::instruction::RegisterMint {
            symbol: SYMBOL.to_string(),
        }
        .data(),
    }
}

fn ix_record_activation(cranker: &Pubkey, mint: &Pubkey) -> Instruction {
    let sequence = {
        // The receipt PDA is seeded with the sequence the record currently holds.
        let record = registry_and_record_for_sequence(mint);
        record
    };
    Instruction {
        program_id: record_date::ID,
        accounts: vec![
            AccountMeta::new(*cranker, true),
            AccountMeta::new(registry_pda().0, false),
            AccountMeta::new_readonly(*mint, false),
            AccountMeta::new(token_record_pda(mint).0, false),
            AccountMeta::new(receipt_pda(mint, sequence).0, false),
            AccountMeta::new_readonly(system_program::ID, false),
        ],
        data: record_date::instruction::RecordActivation {}.data(),
    }
}

/// How many activations the program has recorded for this mint. Read from the VM by the caller
/// in practice; the tests pass it explicitly where it matters.
fn registry_and_record_for_sequence(_mint: &Pubkey) -> u64 {
    0
}

fn ix_read(mint: &Pubkey, which: RecordDateInstruction) -> Instruction {
    let data = match which {
        RecordDateInstruction::Entitlement(raw) => {
            record_date::instruction::ReadEntitlement { raw_amount: raw }.data()
        }
        RecordDateInstruction::Pending => record_date::instruction::ActivationPending {}.data(),
        RecordDateInstruction::Window => record_date::instruction::SettlementWindow {}.data(),
    };
    Instruction {
        program_id: record_date::ID,
        accounts: vec![
            AccountMeta::new_readonly(*mint, false),
            AccountMeta::new_readonly(token_record_pda(mint).0, false),
        ],
        data,
    }
}

enum RecordDateInstruction {
    Entitlement(u64),
    Pending,
    Window,
}

// ---------------------------------------------------------------- reading state

fn read_registry(svm: &LiteSVM) -> Registry {
    let account = svm.get_account(&registry_pda().0).expect("registry exists");
    Registry::try_deserialize(&mut &account.data[..]).expect("registry deserialises")
}

fn read_record(svm: &LiteSVM, mint: &Pubkey) -> TokenRecord {
    let account = svm.get_account(&token_record_pda(mint).0).expect("record exists");
    TokenRecord::try_deserialize(&mut &account.data[..]).expect("record deserialises")
}

fn read_receipt(svm: &LiteSVM, mint: &Pubkey, sequence: u64) -> Receipt {
    let account = svm
        .get_account(&receipt_pda(mint, sequence).0)
        .expect("receipt exists");
    Receipt::try_deserialize(&mut &account.data[..]).expect("receipt deserialises")
}

/// Overwrite the mint's multiplier fields, as a new corporate action would.
fn set_multiplier(svm: &mut LiteSVM, mint: &Pubkey, base: f64, new: f64, effective_at: i64) {
    let mut account = svm.get_account(mint).expect("mint exists");
    // header at 275, body at 279, then authority(32), multiplier(8), timestamp(8), new(8)
    let body = HEADER_OFFSET + 4 + 32;
    account.data[body..body + 8].copy_from_slice(&base.to_le_bytes());
    account.data[body + 8..body + 16].copy_from_slice(&effective_at.to_le_bytes());
    account.data[body + 16..body + 24].copy_from_slice(&new.to_le_bytes());
    svm.set_account(*mint, account).expect("the mint is written");
}

// ---------------------------------------------------------------- the real mint account

#[test]
fn the_fixture_is_the_real_account_it_claims_to_be() {
    assert_eq!(MINT_BYTES.len(), ACCOUNT_LEN);
    assert_eq!(MINT_BYTES[44], DECIMALS);
    assert_eq!(
        u64::from_le_bytes(MINT_BYTES[36..44].try_into().unwrap()),
        SUPPLY
    );

    // The layout the parser assumes, asserted on the real account rather than on a synthetic one.
    // A mint fills only the first 82 bytes of the 165-byte base region; the 83 bytes after it are
    // padding and must be zero; the account-type byte is at 165 and says 1 (a mint); the first
    // extension header is at 166.
    assert_eq!(
        &MINT_BYTES[token2022::MINT_BASE_LEN..token2022::BASE_REGION_LEN],
        &[0u8; 83][..],
        "the 83 bytes between the mint base state and the account-type byte are padding"
    );
    assert_eq!(
        MINT_BYTES[token2022::BASE_REGION_LEN],
        token2022::ACCOUNT_TYPE_MINT,
        "the account-type byte says this is a mint"
    );
    assert_eq!(token2022::TLV_START, 166, "the first extension header is at 166");

    // The mint authority is a COption, so the first four bytes are its tag. It is Some on this
    // mint, which is worth pinning: the issuer can still mint, so the supply the page divides by
    // is not fixed. A tag of anything other than 0 or 1 would mean the layout is not what is
    // assumed, and every offset below it would be wrong.
    let authority_tag = u32::from_le_bytes(MINT_BYTES[0..4].try_into().unwrap());
    assert!(
        authority_tag <= 1,
        "the mint authority COption tag is {authority_tag}, which is not a valid COption"
    );
    let freeze_tag = u32::from_le_bytes(MINT_BYTES[46..50].try_into().unwrap());
    assert!(
        freeze_tag <= 1,
        "the freeze authority COption tag is {freeze_tag}, which is not a valid COption"
    );
}

#[test]
fn the_parser_finds_the_extension_at_byte_275_not_byte_83() {
    let offset = token2022::find_scaled_ui_amount(MINT_BYTES).expect("the extension is found");

    assert_eq!(
        offset - 4,
        HEADER_OFFSET,
        "reading the documented layout as '82-byte base, then extensions' puts the first header \
         at byte 83. It is at 166, because the base region is a fixed 165 bytes and the mint \
         fills only the first 82 of them. The scaled extension is the fourth, at 275."
    );
    // The first header really is at 166, and the three before it are the extensions the issuer
    // registered ahead of the scaled one.
    let first = u16::from_le_bytes([MINT_BYTES[token2022::TLV_START], MINT_BYTES[token2022::TLV_START + 1]]);
    assert_eq!(first, 18, "MetadataPointer is the first extension");
    let walk = [
        (token2022::TLV_START, 18u16, 64usize),
        (234, 12, 32),
        (270, 6, 1),
        (275, 25, 56),
    ];
    let mut at = token2022::TLV_START;
    for (expect_at, expect_type, expect_len) in walk {
        assert_eq!(at, expect_at, "the walk reaches {expect_at}");
        let t = u16::from_le_bytes([MINT_BYTES[at], MINT_BYTES[at + 1]]);
        let l = u16::from_le_bytes([MINT_BYTES[at + 2], MINT_BYTES[at + 3]]);
        assert_eq!((t, l as usize), (expect_type, expect_len));
        at += 4 + l as usize;
    }
    let ext_type = u16::from_le_bytes([MINT_BYTES[HEADER_OFFSET], MINT_BYTES[HEADER_OFFSET + 1]]);
    let ext_len = u16::from_le_bytes([
        MINT_BYTES[HEADER_OFFSET + 2],
        MINT_BYTES[HEADER_OFFSET + 3],
    ]);
    assert_eq!(ext_type, EXTENSION_TYPE);
    assert_eq!(ext_len, EXTENSION_LENGTH);
}

#[test]
fn the_parser_reads_the_values_the_mint_actually_holds() {
    let mint = token2022::read_mint(MINT_BYTES).expect("the mint parses");

    assert_eq!(mint.decimals, DECIMALS);
    assert_eq!(mint.supply, SUPPLY);
    assert_eq!(mint.scaled_ui_amount.multiplier, BASE_MULTIPLIER);
    assert_eq!(mint.scaled_ui_amount.new_multiplier, LIVE_MULTIPLIER);
    assert_eq!(
        mint.scaled_ui_amount.new_multiplier_effective_timestamp,
        EFFECTIVE_TIMESTAMP
    );
}

#[test]
fn the_two_multipliers_are_not_the_same_number() {
    let mint = token2022::read_mint(MINT_BYTES).expect("the mint parses");
    let scaled = &mint.scaled_ui_amount;

    assert_ne!(
        scaled.multiplier, scaled.new_multiplier,
        "this fixture is only useful because the two fields differ"
    );
    // The read rule. Before the timestamp the old value is live; at and after it, the new one.
    assert_eq!(
        scaled.effective(EFFECTIVE_TIMESTAMP - 1),
        BASE_MULTIPLIER,
        "the new multiplier must not be live before its timestamp"
    );
    assert_eq!(scaled.effective(EFFECTIVE_TIMESTAMP), LIVE_MULTIPLIER);
    assert_eq!(scaled.effective(NOW), LIVE_MULTIPLIER);
    assert_eq!(scaled.effective_at(NOW), EFFECTIVE_TIMESTAMP);
}

// ---------------------------------------------------------------- the instructions

#[test]
fn init_registry_writes_the_authority() {
    let (mut svm, payer, _) = setup();

    send_ok(&mut svm, &payer, ix_init_registry(&payer.pubkey()));

    let registry = read_registry(&svm);
    assert_eq!(registry.authority, payer.pubkey());
    assert_eq!(registry.mints, 0);
    assert_eq!(registry.activations, 0);
}

#[test]
fn register_mint_keeps_the_stale_field_beside_the_live_one() {
    let (mut svm, payer, mint) = setup();
    send_ok(&mut svm, &payer, ix_init_registry(&payer.pubkey()));
    send_ok(&mut svm, &payer, ix_register_mint(&payer.pubkey(), &mint));

    let record = read_record(&svm, &mint);
    assert_eq!(record.mint, mint);
    assert_eq!(&record.symbol[..record.symbol_len as usize], SYMBOL.as_bytes());
    assert_eq!(record.decimals, DECIMALS);
    assert_eq!(record.base_multiplier_bits, BASE_MULTIPLIER.to_bits());
    assert_eq!(record.live_multiplier_bits, LIVE_MULTIPLIER.to_bits());
    assert_eq!(
        record.live_multiplier_fp,
        token2022::to_fixed_point(LIVE_MULTIPLIER).unwrap()
    );
    assert_eq!(record.live_effective_at, EFFECTIVE_TIMESTAMP);
    assert_eq!(record.activations, 0);
    assert_eq!(read_registry(&svm).mints, 1);

    // The point of keeping both: a reader can see that the field the mint names `multiplier` is
    // not the multiplier, and by how much.
    assert_ne!(record.base_multiplier_bits, record.live_multiplier_bits);
}

#[test]
fn register_mint_refuses_an_account_token_2022_does_not_own() {
    let (mut svm, payer, _) = setup();
    send_ok(&mut svm, &payer, ix_init_registry(&payer.pubkey()));

    let impostor = Pubkey::new_unique();
    svm.set_account(
        impostor,
        Account {
            lamports: 1_000_000_000,
            data: MINT_BYTES.to_vec(),
            owner: system_program::ID,
            executable: false,
            rent_epoch: 0,
        },
    )
    .unwrap();

    expect_anchor_error(
        &mut svm,
        &payer,
        ix_register_mint(&payer.pubkey(), &impostor),
        RecordDateError::NotToken2022,
    );
}

#[test]
fn record_activation_refuses_when_the_multiplier_has_not_moved() {
    let (mut svm, payer, mint) = setup();
    send_ok(&mut svm, &payer, ix_init_registry(&payer.pubkey()));
    send_ok(&mut svm, &payer, ix_register_mint(&payer.pubkey(), &mint));

    // register_mint stored the live multiplier, so there is nothing new to record.
    expect_anchor_error(
        &mut svm,
        &payer,
        ix_record_activation(&payer.pubkey(), &mint),
        RecordDateError::NothingToRecord,
    );
}

#[test]
fn record_activation_writes_a_receipt_when_the_multiplier_moves() {
    let (mut svm, payer, mint) = setup();
    send_ok(&mut svm, &payer, ix_init_registry(&payer.pubkey()));
    send_ok(&mut svm, &payer, ix_register_mint(&payer.pubkey(), &mint));

    // A second corporate action: the live multiplier becomes the previous one, and a higher
    // value takes over. This is exactly the shape the issuer ships.
    let second = LIVE_MULTIPLIER * 1.004;
    let second_at = NOW - 60;
    set_multiplier(&mut svm, &mint, LIVE_MULTIPLIER, second, second_at);

    send_ok(&mut svm, &payer, ix_record_activation(&payer.pubkey(), &mint));

    let receipt = read_receipt(&svm, &mint, 0);
    assert_eq!(receipt.mint, mint);
    assert_eq!(receipt.sequence, 0);
    assert_eq!(receipt.previous_bits, LIVE_MULTIPLIER.to_bits());
    assert_eq!(receipt.new_bits, second.to_bits());
    assert_eq!(receipt.effective_at, second_at);
    assert_eq!(receipt.raw_supply, SUPPLY);
    assert!(!receipt.is_flat(), "the receipt records a real change");

    let record = read_record(&svm, &mint);
    assert_eq!(record.activations, 1);
    assert_eq!(record.live_multiplier_bits, second.to_bits());
    assert_eq!(read_registry(&svm).activations, 1);
}

#[test]
fn a_second_activation_gets_its_own_receipt_in_sequence() {
    let (mut svm, payer, mint) = setup();
    send_ok(&mut svm, &payer, ix_init_registry(&payer.pubkey()));
    send_ok(&mut svm, &payer, ix_register_mint(&payer.pubkey(), &mint));

    let second = LIVE_MULTIPLIER * 1.004;
    set_multiplier(&mut svm, &mint, LIVE_MULTIPLIER, second, NOW - 120);
    send_ok(&mut svm, &payer, ix_record_activation(&payer.pubkey(), &mint));

    let third = second * 1.003;
    set_multiplier(&mut svm, &mint, second, third, NOW - 60);
    // Sequence 1, so the PDA is a different account.
    let ix = Instruction {
        program_id: record_date::ID,
        accounts: vec![
            AccountMeta::new(payer.pubkey(), true),
            AccountMeta::new(registry_pda().0, false),
            AccountMeta::new_readonly(mint, false),
            AccountMeta::new(token_record_pda(&mint).0, false),
            AccountMeta::new(receipt_pda(&mint, 1).0, false),
            AccountMeta::new_readonly(system_program::ID, false),
        ],
        data: record_date::instruction::RecordActivation {}.data(),
    };
    send_ok(&mut svm, &payer, ix);

    let first = read_receipt(&svm, &mint, 0);
    let second_receipt = read_receipt(&svm, &mint, 1);
    assert_eq!(first.sequence, 0);
    assert_eq!(second_receipt.sequence, 1);
    assert_eq!(second_receipt.previous_bits, second.to_bits());
    assert_eq!(second_receipt.new_bits, third.to_bits());
    assert_eq!(read_record(&svm, &mint).activations, 2);
    assert_eq!(read_registry(&svm).activations, 2);
}

#[test]
fn read_entitlement_scales_a_raw_amount() {
    let (mut svm, payer, mint) = setup();
    send_ok(&mut svm, &payer, ix_init_registry(&payer.pubkey()));
    send_ok(&mut svm, &payer, ix_register_mint(&payer.pubkey(), &mint));

    let raw: u64 = 4_000_000_000;
    let meta = send_ok(
        &mut svm,
        &payer,
        ix_read(&mint, RecordDateInstruction::Entitlement(raw)),
    );

    let returned = meta.return_data.data;
    assert_eq!(returned.len(), 16, "a u128 is sixteen bytes");
    let mut bytes = [0u8; 16];
    bytes.copy_from_slice(&returned);
    let scaled = u128::from_le_bytes(bytes);

    let expected = token2022::scaled_amount(raw, token2022::to_fixed_point(LIVE_MULTIPLIER).unwrap())
        .unwrap();
    assert_eq!(scaled, expected);
    assert!(scaled > raw as u128, "the multiplier is above one, so the scaled amount is larger");
}

#[test]
fn activation_pending_answers_one_only_after_a_move() {
    let (mut svm, payer, mint) = setup();
    send_ok(&mut svm, &payer, ix_init_registry(&payer.pubkey()));
    send_ok(&mut svm, &payer, ix_register_mint(&payer.pubkey(), &mint));

    let meta = send_ok(&mut svm, &payer, ix_read(&mint, RecordDateInstruction::Pending));
    assert_eq!(meta.return_data.data, vec![0], "nothing has moved yet");

    set_multiplier(&mut svm, &mint, LIVE_MULTIPLIER, LIVE_MULTIPLIER * 1.004, NOW - 30);
    let meta = send_ok(&mut svm, &payer, ix_read(&mint, RecordDateInstruction::Pending));
    assert_eq!(meta.return_data.data, vec![1], "the multiplier moved");

    send_ok(&mut svm, &payer, ix_record_activation(&payer.pubkey(), &mint));
    let meta = send_ok(&mut svm, &payer, ix_read(&mint, RecordDateInstruction::Pending));
    assert_eq!(meta.return_data.data, vec![0], "and now it is recorded");
}

#[test]
fn settlement_window_reports_seconds_since_the_activation() {
    let (mut svm, payer, mint) = setup();
    send_ok(&mut svm, &payer, ix_init_registry(&payer.pubkey()));
    send_ok(&mut svm, &payer, ix_register_mint(&payer.pubkey(), &mint));

    // The mint's activation is the one already in the fixture, six days before NOW.
    let meta = send_ok(&mut svm, &payer, ix_read(&mint, RecordDateInstruction::Window));
    let mut bytes = [0u8; 8];
    bytes.copy_from_slice(&meta.return_data.data);
    let delta = i64::from_le_bytes(bytes);
    assert_eq!(delta, NOW - EFFECTIVE_TIMESTAMP);
    assert!(
        delta.abs() > RECOMMENDED_PAUSE_SECS,
        "six days is well outside the recommended pause"
    );

    // Now put an activation inside the pause and check the sign flips.
    let just_after = NOW - (RECOMMENDED_PAUSE_SECS - 60);
    set_multiplier(&mut svm, &mint, LIVE_MULTIPLIER, LIVE_MULTIPLIER * 1.004, just_after);
    let meta = send_ok(&mut svm, &payer, ix_read(&mint, RecordDateInstruction::Window));
    let mut bytes = [0u8; 8];
    bytes.copy_from_slice(&meta.return_data.data);
    let delta = i64::from_le_bytes(bytes);
    assert_eq!(delta, RECOMMENDED_PAUSE_SECS - 60);
    assert!(delta.abs() <= RECOMMENDED_PAUSE_SECS);
}

// ---------------------------------------------------------------- Pyth
//
// The two fixtures are real `PriceUpdateV2` accounts read off chain, and they were picked for what
// they disagree about. Both are the AAPL/USD feed, both are 134 bytes, both carry the same
// discriminator, and both parse into a plausible price. One was published thirty seconds before it
// was captured; the other had been sitting unchanged for 33.6 days because the publisher stopped
// updating that cluster. That difference is the whole point of the staleness test below: the
// account being refused has perfectly good numbers, so age is the only thing that can refuse it.
//
// A fixture written by hand could not support that test. It could be made stale, but it would be
// stale because a test said so rather than because a publisher stopped.

const PYTH_MAINNET_BYTES: &[u8] = include_bytes!("fixtures/pyth_aapl_mainnet.bin");
const PYTH_DEVNET_BYTES: &[u8] = include_bytes!("fixtures/pyth_aapl_devnet.bin");

/// The real account addresses, so the fixtures' provenance is load-bearing rather than decorative:
/// these are the accounts the JSON provenance files name, and they are distinct.
const PYTH_MAINNET_ACCOUNT: &str = "D9uk39pqZMcnmtPP9WeC8cREUpKZmyXLga9mSQ79SphW";
const PYTH_DEVNET_ACCOUNT: &str = "DJgUxxLVN1QxAXykPzpH5Kz5feo1bTCBDipGRUYYqMw";

const AAPL_FEED_ID_HEX: &str = "49f6b65cb1de6b10eaf75e7c03ca029c306d0357e91b5311b175084a5ad55688";

const MAINNET_PUBLISH_TIME: i64 = 1_789_641_059;
const DEVNET_PUBLISH_TIME: i64 = 1_786_737_619;

/// The mainnet account's price, 334.25, as 1e18 fixed point. Asserted, not computed, so a change
/// in the fixture or the conversion has to be noticed rather than absorbed.
const MAINNET_PRICE_FP: u128 = 334_250_000_000_000_000_000;
/// The devnet account's price, 305.92.
const DEVNET_PRICE_FP: u128 = 305_920_000_000_000_000_000;

/// A clock thirty seconds after the mainnet publish.
///
/// This is the one number that turns two similar fixtures into an accept case and a refuse case.
/// At it the mainnet price is 30 seconds old and the devnet one is 33.6 days old.
const PYTH_NOW: i64 = MAINNET_PUBLISH_TIME + 30;

fn feed_id(hex: &str) -> [u8; 32] {
    let bytes = hex.as_bytes();
    assert_eq!(bytes.len(), 64, "a feed id is 32 bytes of hex");
    let mut out = [0u8; 32];
    for i in 0..32 {
        let pair = std::str::from_utf8(&bytes[i * 2..i * 2 + 2]).unwrap();
        out[i] = u8::from_str_radix(pair, 16).expect("the feed id is hex");
    }
    out
}

fn pyth_binding_pda(mint: &Pubkey) -> (Pubkey, u8) {
    Pubkey::find_program_address(&[PYTH_BINDING_SEED, mint.as_ref()], &record_date::ID)
}

/// Move the VM clock. `setup()` fixes one near the mint's activation, which is a different era from
/// a price published minutes ago, so the Pyth tests have to set their own.
fn set_clock(svm: &mut LiteSVM, unix_timestamp: i64) {
    let mut clock: Clock = svm.get_sysvar();
    clock.unix_timestamp = unix_timestamp;
    svm.set_sysvar(&clock);
}

/// Put a captured price account in the VM, owned by the receiver program as it is on chain.
fn install_price_update(svm: &mut LiteSVM, address: Pubkey, bytes: &[u8]) {
    svm.set_account(
        address,
        Account {
            lamports: 1_000_000_000,
            data: bytes.to_vec(),
            owner: pyth::PYTH_RECEIVER_PROGRAM_ID,
            executable: false,
            rent_epoch: 0,
        },
    )
    .expect("the price update account is set");
}

fn read_binding(svm: &LiteSVM, mint: &Pubkey) -> PythBinding {
    let account = svm
        .get_account(&pyth_binding_pda(mint).0)
        .expect("binding exists");
    PythBinding::try_deserialize(&mut &account.data[..]).expect("binding deserialises")
}

// ---------------------------------------------------------------- Pyth instruction builders

fn ix_bind_pyth_feed(payer: &Pubkey, mint: &Pubkey, feed: [u8; 32], symbol: &str) -> Instruction {
    Instruction {
        program_id: record_date::ID,
        accounts: vec![
            AccountMeta::new(*payer, true),
            AccountMeta::new_readonly(*mint, false),
            AccountMeta::new_readonly(token_record_pda(mint).0, false),
            AccountMeta::new(pyth_binding_pda(mint).0, false),
            AccountMeta::new_readonly(system_program::ID, false),
        ],
        data: record_date::instruction::BindPythFeed {
            feed_id: feed,
            feed_symbol: symbol.to_string(),
        }
        .data(),
    }
}

fn ix_verify_against_pyth(
    mint: &Pubkey,
    price_update: &Pubkey,
    expected_price_fp: u128,
    tolerance_bps: u16,
    max_age_secs: i64,
) -> Instruction {
    Instruction {
        program_id: record_date::ID,
        accounts: vec![
            AccountMeta::new_readonly(*mint, false),
            AccountMeta::new_readonly(token_record_pda(mint).0, false),
            AccountMeta::new_readonly(pyth_binding_pda(mint).0, false),
            AccountMeta::new_readonly(*price_update, false),
        ],
        data: record_date::instruction::VerifyAgainstPyth {
            expected_price_fp,
            tolerance_bps,
            max_age_secs,
        }
        .data(),
    }
}

/// The common case: the recommended age limit, a tolerance wide enough to accept the caller's own
/// number, and the price the caller says it is about to settle at.
fn verify_ok(mint: &Pubkey, price_update: &Pubkey, expected_price_fp: u128) -> Instruction {
    ix_verify_against_pyth(
        mint,
        price_update,
        expected_price_fp,
        50,
        pyth::RECOMMENDED_PRICE_AGE_SECS,
    )
}

/// A registered mint bound to the AAPL feed, the mainnet price account installed, and the clock at
/// `PYTH_NOW`. The mint is the NVDAx one because it is the mint this repository has a real account
/// for; what is under test is the price check, not the mapping from ticker to feed.
fn setup_pyth() -> (LiteSVM, Keypair, Pubkey, Pubkey) {
    let (mut svm, payer, mint) = setup();
    set_clock(&mut svm, PYTH_NOW);
    send_ok(&mut svm, &payer, ix_init_registry(&payer.pubkey()));
    send_ok(&mut svm, &payer, ix_register_mint(&payer.pubkey(), &mint));

    let price_update: Pubkey = PYTH_MAINNET_ACCOUNT.parse().expect("the address parses");
    install_price_update(&mut svm, price_update, PYTH_MAINNET_BYTES);
    send_ok(
        &mut svm,
        &payer,
        ix_bind_pyth_feed(&payer.pubkey(), &mint, feed_id(AAPL_FEED_ID_HEX), "AAPL"),
    );
    (svm, payer, mint, price_update)
}

// ---------------------------------------------------------------- the derived layout

#[test]
fn the_pyth_discriminator_is_what_anchor_would_compute() {
    // The constant in the program is written out because a `const` cannot call sha256. This is the
    // check that it was written down correctly, which is what makes the rest of the layout
    // trustworthy: the discriminator passing is what confirms the byte offsets below it.
    let mut hasher = Sha256::new();
    hasher.update(b"account:PriceUpdateV2");
    let digest = hasher.finalize();
    assert_eq!(
        &digest[..8],
        &pyth::DISCRIMINATOR[..],
        "the discriminator does not match sha256(\"account:PriceUpdateV2\")[:8]"
    );
}

#[test]
fn the_price_fixtures_are_the_real_accounts_they_claim_to_be() {
    assert_eq!(PYTH_MAINNET_BYTES.len(), 134);
    assert_eq!(PYTH_DEVNET_BYTES.len(), 134);

    // The accounts are 134 bytes but the parsed fields stop at 133. The last byte's purpose has
    // not been established, so it is tolerated rather than named. This asserts the tolerance is
    // real and the parser does not require the account to end where its fields do.
    assert_eq!(pyth::PARSED_LEN, 133);
    assert!(PYTH_MAINNET_BYTES.len() > pyth::PARSED_LEN);

    assert_eq!(&PYTH_MAINNET_BYTES[..8], &pyth::DISCRIMINATOR);
    assert_eq!(&PYTH_DEVNET_BYTES[..8], &pyth::DISCRIMINATOR);

    let mainnet: Pubkey = PYTH_MAINNET_ACCOUNT.parse().expect("the mainnet address parses");
    let devnet: Pubkey = PYTH_DEVNET_ACCOUNT.parse().expect("the devnet address parses");
    assert_ne!(
        mainnet, devnet,
        "two fixtures that were the same account would not be two fixtures"
    );
}

#[test]
fn the_parser_reads_the_values_the_capture_recorded() {
    let update = pyth::read(PYTH_MAINNET_BYTES).expect("the mainnet fixture parses");

    assert_eq!(update.price, 33_425_000);
    assert_eq!(update.exponent, -5, "equity feeds publish a negative exponent");
    assert_eq!(update.publish_time, MAINNET_PUBLISH_TIME);
    assert_eq!(update.feed_id, feed_id(AAPL_FEED_ID_HEX));
    assert_eq!(update.conf, 5_000);
    assert_eq!(update.posted_slot, 447_772_992);
    assert_eq!(update.verification_level, 1, "1 is Full");

    // The exponent has to be applied as a signed value. Read as unsigned it would be a number with
    // thirty digits, which is why this is asserted as a range as well as a value.
    let human = update.price_at_exponent();
    assert!(
        (human - 334.25).abs() < 1e-9,
        "the human price came out as {human}, not 334.25"
    );
    assert_eq!(update.price_fp().unwrap(), MAINNET_PRICE_FP);

    // And the devnet account parses just as cleanly. It is the age that will be refused, not the
    // parse, which is what makes the staleness test below a test of staleness.
    let stale = pyth::read(PYTH_DEVNET_BYTES).expect("the devnet fixture parses");
    assert_eq!(stale.price_fp().unwrap(), DEVNET_PRICE_FP);
    assert_eq!(stale.feed_id, feed_id(AAPL_FEED_ID_HEX));
    assert_eq!(stale.publish_time, DEVNET_PUBLISH_TIME);
}

#[test]
fn the_parser_refuses_bytes_that_are_not_a_price_update() {
    // Each case mutates the real account in one way, so the parser is required to notice that
    // specific thing rather than to fail for a reason of its own choosing.
    let mut wrong_discriminator = PYTH_MAINNET_BYTES.to_vec();
    wrong_discriminator[0] ^= 0xff;
    assert_eq!(
        pyth::read(&wrong_discriminator).unwrap_err(),
        error!(RecordDateError::NotAPriceUpdate)
    );

    // A `memcmp` filter on a cluster matches any account with those bytes at that offset,
    // including accounts too short to be this type. Length is checked first, so a 132-byte account
    // fails as short even though its discriminator is correct.
    assert_eq!(
        pyth::read(&PYTH_MAINNET_BYTES[..132]).unwrap_err(),
        error!(RecordDateError::PriceUpdateTooShort)
    );

    // The empty account, which is what a rejected candidate can look like.
    assert_eq!(
        pyth::read(&[]).unwrap_err(),
        error!(RecordDateError::PriceUpdateTooShort)
    );

    // The control for the control: the unmutated bytes parse, so the three failures above are
    // caused by the mutations and not by the fixture being unreadable to begin with.
    assert!(pyth::read(PYTH_MAINNET_BYTES).is_ok());
}

#[test]
fn a_price_from_the_future_is_refused() {
    // `setup()` fixes the clock at the mint's activation era, which is 122,459 seconds *before*
    // the mainnet price was published. A clock that is behind the price is a misconfiguration or a
    // forged account, so the price must not be usable. This branch is easy to leave untested
    // because it only fires when the clock and the fixture come from different periods, which is
    // exactly what happened here and nearly went unnoticed.
    let (mut svm, payer, mint) = setup();
    let price_update: Pubkey = PYTH_MAINNET_ACCOUNT.parse().unwrap();
    install_price_update(&mut svm, price_update, PYTH_MAINNET_BYTES);

    send_ok(&mut svm, &payer, ix_init_registry(&payer.pubkey()));
    send_ok(&mut svm, &payer, ix_register_mint(&payer.pubkey(), &mint));
    send_ok(
        &mut svm,
        &payer,
        ix_bind_pyth_feed(&payer.pubkey(), &mint, feed_id(AAPL_FEED_ID_HEX), "AAPL"),
    );

    let update = pyth::read(PYTH_MAINNET_BYTES).unwrap();
    let age = update.age(NOW);
    assert!(
        age < -pyth::MAX_CLOCK_SKEW_SECS,
        "this test only means something while the fixture is ahead of setup()'s clock; the age is \
         {age}s and the skew allowance is {}s",
        pyth::MAX_CLOCK_SKEW_SECS
    );

    expect_anchor_error(
        &mut svm,
        &payer,
        verify_ok(&mint, &price_update, MAINNET_PRICE_FP),
        RecordDateError::StalePythPrice,
    );

    // The same instruction on the same accounts succeeds once the clock is put right, so the
    // refusal above is the clock and not something else about the accounts.
    set_clock(&mut svm, PYTH_NOW);
    send_ok(
        &mut svm,
        &payer,
        verify_ok(&mint, &price_update, MAINNET_PRICE_FP),
    );
}

// ---------------------------------------------------------------- binding a feed

#[test]
fn bind_pyth_feed_writes_the_feed_id_and_survives_a_second_run() {
    let (mut svm, payer, mint) = setup();
    send_ok(&mut svm, &payer, ix_init_registry(&payer.pubkey()));
    send_ok(&mut svm, &payer, ix_register_mint(&payer.pubkey(), &mint));

    send_ok(
        &mut svm,
        &payer,
        ix_bind_pyth_feed(&payer.pubkey(), &mint, feed_id(AAPL_FEED_ID_HEX), "AAPL"),
    );

    let binding = read_binding(&svm, &mint);
    assert_eq!(binding.mint, mint);
    assert_eq!(binding.feed_id, feed_id(AAPL_FEED_ID_HEX));
    assert_eq!(
        &binding.feed_symbol[..binding.feed_symbol_len as usize],
        b"AAPL"
    );
    assert_eq!(binding.bump, pyth_binding_pda(&mint).1);
    assert_eq!(binding.bound_at, NOW);

    // The feed id is what is stored, not the price account's address: a `PriceUpdateV2` account is
    // rewritten on every publish and its address is not stable, so binding to an address would
    // break on the next update.
    let price_account: Pubkey = PYTH_MAINNET_ACCOUNT.parse().unwrap();
    assert_ne!(
        binding.feed_id,
        price_account.to_bytes(),
        "the binding stores the feed id, not the price account's address"
    );

    // Rebinding overwrites rather than failing, which is what makes a demo re-runnable and a
    // corrected feed id fixable. `init_if_needed` is the reason; with plain `init` this would
    // fail on the account already existing.
    let other = {
        let mut id = feed_id(AAPL_FEED_ID_HEX);
        id[31] ^= 0x01;
        id
    };
    send_ok(
        &mut svm,
        &payer,
        ix_bind_pyth_feed(&payer.pubkey(), &mint, other, "OTHER"),
    );

    let rebound = read_binding(&svm, &mint);
    assert_eq!(rebound.feed_id, other);
    assert_eq!(
        &rebound.feed_symbol[..rebound.feed_symbol_len as usize],
        b"OTHER"
    );
    assert_eq!(
        rebound.feed_symbol_len, 5,
        "the longer previous symbol must not survive in the length field"
    );
}

#[test]
fn bind_pyth_feed_refuses_a_symbol_longer_than_the_state_allows() {
    let (mut svm, payer, mint) = setup();
    send_ok(&mut svm, &payer, ix_init_registry(&payer.pubkey()));
    send_ok(&mut svm, &payer, ix_register_mint(&payer.pubkey(), &mint));

    // SYMBOL_MAX is 12. The 12-byte case must pass, or the 13-byte failure below would prove
    // nothing about the boundary.
    send_ok(
        &mut svm,
        &payer,
        ix_bind_pyth_feed(&payer.pubkey(), &mint, feed_id(AAPL_FEED_ID_HEX), "0123456789ab"),
    );

    expect_anchor_error(
        &mut svm,
        &payer,
        ix_bind_pyth_feed(&payer.pubkey(), &mint, feed_id(AAPL_FEED_ID_HEX), "0123456789abc"),
        RecordDateError::FeedSymbolTooLong,
    );
}

#[test]
fn bind_pyth_feed_refuses_a_mint_that_was_never_registered() {
    let (mut svm, payer, _) = setup();
    send_ok(&mut svm, &payer, ix_init_registry(&payer.pubkey()));

    // A mint whose token record was never written. The binding PDA is derived from the mint key
    // and the token record is a constraint on the accounts struct, so this is refused before the
    // handler runs.
    let unregistered = Pubkey::new_unique();
    let failed = send(
        &mut svm,
        &payer,
        ix_bind_pyth_feed(
            &payer.pubkey(),
            &unregistered,
            feed_id(AAPL_FEED_ID_HEX),
            "AAPL",
        ),
    )
    .expect_err("binding a mint that was never registered must fail");

    assert!(
        failed.meta.pretty_logs().contains("token_record"),
        "expected the missing token record to be named in the logs:\n{}",
        failed.meta.pretty_logs()
    );
}

// ---------------------------------------------------------------- verifying a price

#[test]
fn verify_against_pyth_accepts_the_live_price_and_returns_it() {
    let (mut svm, payer, mint, price_update) = setup_pyth();

    let meta = send_ok(
        &mut svm,
        &payer,
        verify_ok(&mint, &price_update, MAINNET_PRICE_FP),
    );

    let returned = meta.return_data.data;
    assert_eq!(returned.len(), 32, "a price and a deviation, sixteen bytes each");
    let mut price_bytes = [0u8; 16];
    price_bytes.copy_from_slice(&returned[..16]);
    assert_eq!(
        u128::from_le_bytes(price_bytes),
        MAINNET_PRICE_FP,
        "the returned price is Pyth's, not the caller's"
    );
    let mut deviation_bytes = [0u8; 16];
    deviation_bytes.copy_from_slice(&returned[16..]);
    assert_eq!(
        u128::from_le_bytes(deviation_bytes),
        0,
        "the caller passed Pyth's own number, so there is nothing to deviate by"
    );
}

#[test]
fn verify_against_pyth_refuses_the_stale_account_even_at_the_most_permissive_setting() {
    let (mut svm, payer, mint, _) = setup_pyth();

    // The devnet account, installed in place of the mainnet one. It is the same feed, the same
    // length, the same discriminator, and it parses. Its price is 305.92 and the caller is about to
    // settle at 305.92, so the deviation is zero and the tolerance cannot refuse it.
    let stale_account: Pubkey = PYTH_DEVNET_ACCOUNT.parse().unwrap();
    install_price_update(&mut svm, stale_account, PYTH_DEVNET_BYTES);

    let update = pyth::read(PYTH_DEVNET_BYTES).unwrap();
    let age = update.age(PYTH_NOW);
    assert!(
        age > 30 * 86_400,
        "this test is about staleness, so the fixture has to be stale by a margin no clock skew \
         could explain; it is {age}s old"
    );
    assert!(
        age > pyth::MAX_PRICE_AGE_CEILING_SECS,
        "the claim below is stronger than 'the recommended limit refuses it': it is refused at \
         every setting the program accepts, so the age is {age}s and the ceiling is {}s",
        pyth::MAX_PRICE_AGE_CEILING_SECS
    );
    assert_eq!(
        pyth::deviation_bps(update.price_fp().unwrap(), DEVNET_PRICE_FP),
        0,
        "the numbers agree exactly, so age is the only thing that can refuse this"
    );

    // Refused at the recommended limit.
    expect_anchor_error(
        &mut svm,
        &payer,
        verify_ok(&mint, &stale_account, DEVNET_PRICE_FP),
        RecordDateError::StalePythPrice,
    );

    // And refused at the ceiling, which is the stronger form of the claim: there is no age the
    // caller is allowed to declare that would let this price through.
    expect_anchor_error(
        &mut svm,
        &payer,
        ix_verify_against_pyth(
            &mint,
            &stale_account,
            DEVNET_PRICE_FP,
            50,
            pyth::MAX_PRICE_AGE_CEILING_SECS,
        ),
        RecordDateError::StalePythPrice,
    );
}

#[test]
fn the_age_limit_is_the_callers_to_set_within_the_ceiling() {
    let (mut svm, payer, mint, price_update) = setup_pyth();

    // Six hours past the publish. The account bytes are the real ones, unmodified; only the clock
    // moved, so the age is genuine rather than written into a fixture.
    let six_hours_later = MAINNET_PUBLISH_TIME + 6 * 3600;
    set_clock(&mut svm, six_hours_later);
    assert_eq!(
        pyth::read(PYTH_MAINNET_BYTES).unwrap().age(six_hours_later),
        21_600
    );

    // Refused at the recommendation, because six hours is far past five minutes.
    expect_anchor_error(
        &mut svm,
        &payer,
        verify_ok(&mint, &price_update, MAINNET_PRICE_FP),
        RecordDateError::StalePythPrice,
    );

    // Accepted at six hours, because the caller declared it would settle on a price that old. Same
    // accounts, same price, same tolerance: the age limit is the only difference between this and
    // the refusal above, which is what makes it a test of the limit.
    send_ok(
        &mut svm,
        &payer,
        ix_verify_against_pyth(&mint, &price_update, MAINNET_PRICE_FP, 50, 21_600),
    );

    // And refused again when the caller asks for more than the program will grant, so the ceiling
    // is enforced rather than advisory.
    expect_anchor_error(
        &mut svm,
        &payer,
        ix_verify_against_pyth(
            &mint,
            &price_update,
            MAINNET_PRICE_FP,
            50,
            pyth::MAX_PRICE_AGE_CEILING_SECS + 1,
        ),
        RecordDateError::PriceAgeCeilingExceeded,
    );

    // A negative limit is refused as well. The ceiling is a range and not a maximum, and a caller
    // that passed a negative age would otherwise be asking for a price from the future.
    expect_anchor_error(
        &mut svm,
        &payer,
        ix_verify_against_pyth(&mint, &price_update, MAINNET_PRICE_FP, 50, -1),
        RecordDateError::PriceAgeCeilingExceeded,
    );
}

#[test]
fn verify_against_pyth_refuses_a_price_for_a_different_feed() {
    let (mut svm, payer, mint, price_update) = setup_pyth();

    // A feed id one bit away from the bound one. The check compares thirty-two bytes for equality,
    // so any distinct value exercises it; the real-world shape of this mistake is settling AAPL
    // against a price for a different equity, which is what the mutation stands in for.
    let mut wrong_feed = feed_id(AAPL_FEED_ID_HEX);
    wrong_feed[31] ^= 0x01;
    send_ok(
        &mut svm,
        &payer,
        ix_bind_pyth_feed(&payer.pubkey(), &mint, wrong_feed, "AAPL"),
    );

    // The price account is the right feed, the caller's price is right, and the clock is right.
    // The binding is the only thing that changed.
    expect_anchor_error(
        &mut svm,
        &payer,
        verify_ok(&mint, &price_update, MAINNET_PRICE_FP),
        RecordDateError::FeedIdMismatch,
    );

    // Rebound to the account's real feed, the same instruction passes.
    send_ok(
        &mut svm,
        &payer,
        ix_bind_pyth_feed(&payer.pubkey(), &mint, feed_id(AAPL_FEED_ID_HEX), "AAPL"),
    );
    send_ok(
        &mut svm,
        &payer,
        verify_ok(&mint, &price_update, MAINNET_PRICE_FP),
    );
}

#[test]
fn verify_against_pyth_refuses_an_account_the_receiver_does_not_own() {
    let (mut svm, payer, mint, _) = setup_pyth();

    // The real bytes, the real length, the real discriminator, owned by somebody else. A caller
    // can deploy an account with this exact layout, so ownership is the check that makes the rest
    // of the parse mean anything.
    let impostor = Pubkey::new_unique();
    svm.set_account(
        impostor,
        Account {
            lamports: 1_000_000_000,
            data: PYTH_MAINNET_BYTES.to_vec(),
            owner: system_program::ID,
            executable: false,
            rent_epoch: 0,
        },
    )
    .unwrap();

    expect_anchor_error(
        &mut svm,
        &payer,
        verify_ok(&mint, &impostor, MAINNET_PRICE_FP),
        RecordDateError::NotPythReceiver,
    );
}

#[test]
fn verify_against_pyth_refuses_a_price_outside_the_tolerance() {
    let (mut svm, payer, mint, price_update) = setup_pyth();

    // 400 against a Pyth price of 334.25 is 1,644 basis points apart, well outside any tolerance
    // this instruction would be called with.
    let far_off: u128 = 400_000_000_000_000_000_000;
    assert!(
        pyth::deviation_bps(MAINNET_PRICE_FP, far_off) > 1_000,
        "the case below is only a test of the tolerance if it is genuinely far outside it"
    );

    expect_anchor_error(
        &mut svm,
        &payer,
        verify_ok(&mint, &price_update, far_off),
        RecordDateError::PriceDeviation,
    );
}

#[test]
fn the_tolerance_argument_is_what_decides_between_the_same_two_prices() {
    let (mut svm, payer, mint, price_update) = setup_pyth();

    // 335.25 against 334.25. The two calls below differ only in the tolerance, so whichever way
    // they come out is the tolerance argument and nothing else. A test that only checked the
    // refusal would not distinguish a working tolerance from an instruction that refuses
    // everything.
    let slightly_high: u128 = 335_250_000_000_000_000_000;
    let deviation = pyth::deviation_bps(MAINNET_PRICE_FP, slightly_high);
    assert!(
        deviation > 0 && deviation < 50,
        "the deviation has to sit between the two tolerances below; it is {deviation}bps"
    );

    expect_anchor_error(
        &mut svm,
        &payer,
        ix_verify_against_pyth(
            &mint,
            &price_update,
            slightly_high,
            20,
            pyth::RECOMMENDED_PRICE_AGE_SECS,
        ),
        RecordDateError::PriceDeviation,
    );

    let meta = send_ok(
        &mut svm,
        &payer,
        ix_verify_against_pyth(
            &mint,
            &price_update,
            slightly_high,
            50,
            pyth::RECOMMENDED_PRICE_AGE_SECS,
        ),
    );
    let mut deviation_bytes = [0u8; 16];
    deviation_bytes.copy_from_slice(&meta.return_data.data[16..]);
    assert_eq!(
        u128::from_le_bytes(deviation_bytes),
        deviation,
        "the deviation the caller is told about is the one it was judged on"
    );
}

#[test]
fn verify_against_pyth_refuses_a_mint_that_was_never_registered() {
    let (mut svm, payer, _, price_update) = setup_pyth();

    let unregistered = Pubkey::new_unique();
    let failed = send(
        &mut svm,
        &payer,
        verify_ok(&unregistered, &price_update, MAINNET_PRICE_FP),
    )
    .expect_err("a mint with no record cannot be verified");

    assert!(
        failed.meta.pretty_logs().contains("token_record"),
        "expected the missing token record to be named in the logs:\n{}",
        failed.meta.pretty_logs()
    );
}

#[test]
fn verify_against_pyth_refuses_a_mint_with_no_binding() {
    let (mut svm, payer, mint, price_update) = setup_pyth();

    // A second mint, registered but never bound. It has to be given a real mint account first:
    // `register_mint` checks the owner, so a bare address would fail on that and never reach the
    // state this test is about.
    let other_mint = Pubkey::new_unique();
    svm.set_account(
        other_mint,
        Account {
            lamports: 1_000_000_000,
            data: MINT_BYTES.to_vec(),
            owner: TOKEN_2022_PROGRAM_ID,
            executable: false,
            rent_epoch: 0,
        },
    )
    .unwrap();
    send_ok(&mut svm, &payer, ix_register_mint(&payer.pubkey(), &other_mint));

    let failed = send(
        &mut svm,
        &payer,
        verify_ok(&other_mint, &price_update, MAINNET_PRICE_FP),
    )
    .expect_err("a mint with no binding cannot be verified");

    assert!(
        failed.meta.pretty_logs().contains("binding"),
        "expected the missing binding to be named in the logs:\n{}",
        failed.meta.pretty_logs()
    );

    // And the mint that was bound still verifies, so the failure above is the missing binding
    // rather than the instruction having been broken by the previous call.
    send_ok(
        &mut svm,
        &payer,
        verify_ok(&mint, &price_update, MAINNET_PRICE_FP),
    );
}

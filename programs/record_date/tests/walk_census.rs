//! Runs the TLV walk over a directory of captured mint accounts and prints what it did.
//!
//! This exists to answer one question that a unit test cannot: **does the walk behave the same
//! way on real accounts after a change to it?** A synthesised account is built from the same
//! understanding of the layout as the parser, so it cannot disagree with the parser. A real
//! account can, and this runs over real ones.
//!
//! The captures live in `tests/mints/`, one `*.bin` per mint, 927 of them, taken from the issuer's
//! own mints on mainnet by `scripts/capture_mint_census.py`. They are committed, so a plain
//! `cargo test` walks the whole population with no network and no environment variable:
//!
//! ```text
//! cargo test --test walk_census -- --nocapture
//! ```
//!
//! **This test used to pass vacuously.** It returned early when `RECORD_DATE_MINT_DIR` was unset,
//! and no mint directory was committed, so it reported success having walked nothing. A check that
//! cannot fail is worse than no check, so the directory is now the default rather than an opt-in
//! and a missing directory is a failure. `RECORD_DATE_MINT_DIR` still overrides it, which is how
//! you point it at a fresh capture to diff against the committed one.
//!
//! The output is one line per account, sorted by name, in the form `name offset` or
//! `name ERR <message>`. Capture it before and after a change to `find_scaled_ui_amount` and
//! diff the two: identical output is the claim that the change is inert on real data.

use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;

use record_date::token2022::find_scaled_ui_amount;

/// How many captures the committed set holds. Asserted as a floor rather than an equality so that
/// re-capturing with a mint added upstream does not fail the build, while a truncated commit does.
const COMMITTED_FLOOR: usize = 900;

#[test]
fn walks_every_captured_account() {
    let dir = match std::env::var("RECORD_DATE_MINT_DIR") {
        Ok(dir) => PathBuf::from(dir),
        Err(_) => PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests").join("mints"),
    };
    assert!(
        dir.is_dir(),
        "{} is not a directory. The committed captures are what make this test able to fail; \
         regenerate them with `python3 scripts/capture_mint_census.py`.",
        dir.display()
    );

    let entries = fs::read_dir(&dir)
        .unwrap_or_else(|e| panic!("{} is not readable: {e}", dir.display()));
    let mut names: Vec<PathBuf> = entries
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.extension().is_some_and(|x| x == "bin"))
        .collect();
    names.sort();

    assert!(
        names.len() >= COMMITTED_FLOOR,
        "{} holds {} .bin files, fewer than the {COMMITTED_FLOOR} the committed census should have. \
         A short directory would let this test pass on a fraction of the population.",
        dir.display(),
        names.len()
    );

    let mut ok = 0usize;
    let mut refused = 0usize;
    let mut offsets: BTreeMap<usize, Vec<String>> = BTreeMap::new();
    for path in &names {
        let name = path.file_stem().unwrap().to_string_lossy().to_string();
        let data = fs::read(path).unwrap();
        match find_scaled_ui_amount(&data) {
            Ok(off) => {
                ok += 1;
                println!("{name} {off}");
                offsets.entry(off).or_default().push(name);
            }
            Err(e) => {
                refused += 1;
                println!("{name} ERR {e}");
            }
        }
    }
    println!("census: {} accounts, {ok} walked, {refused} refused", names.len());
    for (off, at) in &offsets {
        println!("  offset {off}: {} accounts", at.len());
    }

    // Every account in a captured set of live mints should walk. A refusal here is not
    // automatically a bug, but it is a change in behaviour and has to be explained rather
    // than noticed later.
    assert_eq!(
        refused, 0,
        "{refused} of {} live mint accounts were refused by the walk",
        names.len()
    );

    // The population is uniform: every xStock mint carries the same extension at the same body
    // offset. That uniformity is what lets the walk be written once against a layout rather than
    // per account, so a single account that reads differently is news and this test is the place
    // it should arrive. The message names the accounts so the diff is not a hunt.
    assert_eq!(
        offsets.len(),
        1,
        "the walk found {} different body offsets across {} accounts: {}",
        offsets.len(),
        names.len(),
        offsets
            .iter()
            .map(|(off, at)| format!(
                "{off} on {} accounts, first {}, last {}",
                at.len(),
                at.first().map(String::as_str).unwrap_or("-"),
                at.last().map(String::as_str).unwrap_or("-")
            ))
            .collect::<Vec<_>>()
            .join("; ")
    );
}

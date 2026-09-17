//! Runs the TLV walk over a directory of captured mint accounts and prints what it did.
//!
//! This exists to answer one question that a unit test cannot: **does the walk behave the same
//! way on real accounts after a change to it?** A synthesised account is built from the same
//! understanding of the layout as the parser, so it cannot disagree with the parser. A real
//! account can, and this runs over real ones.
//!
//! It is inert unless `RECORD_DATE_MINT_DIR` names a directory, so `cargo test` and CI are
//! unaffected. Point it at a directory of `*.bin` files, each one the raw bytes of a mint
//! account, and run:
//!
//! ```text
//! RECORD_DATE_MINT_DIR=/tmp/mints cargo test --test walk_census -- --nocapture
//! ```
//!
//! The output is one line per account, sorted by name, in the form `name offset` or
//! `name ERR <message>`. Capture it before and after a change to `find_scaled_ui_amount` and
//! diff the two: identical output is the claim that the change is inert on real data.

use std::fs;
use std::path::PathBuf;

use record_date::token2022::find_scaled_ui_amount;

#[test]
fn walks_every_captured_account() {
    let Ok(dir) = std::env::var("RECORD_DATE_MINT_DIR") else {
        eprintln!("RECORD_DATE_MINT_DIR is not set; the walk census is skipped");
        return;
    };
    let dir = PathBuf::from(dir);
    let Ok(entries) = fs::read_dir(&dir) else {
        eprintln!("{} is not a readable directory; the walk census is skipped", dir.display());
        return;
    };

    let mut names: Vec<PathBuf> = entries
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.extension().is_some_and(|x| x == "bin"))
        .collect();
    names.sort();

    assert!(
        !names.is_empty(),
        "{} contains no .bin files, so the census would prove nothing",
        dir.display()
    );

    let mut ok = 0usize;
    let mut refused = 0usize;
    for path in &names {
        let name = path.file_stem().unwrap().to_string_lossy().to_string();
        let data = fs::read(path).unwrap();
        match find_scaled_ui_amount(&data) {
            Ok(off) => {
                ok += 1;
                println!("{name} {off}");
            }
            Err(e) => {
                refused += 1;
                println!("{name} ERR {e}");
            }
        }
    }
    println!("census: {} accounts, {ok} walked, {refused} refused", names.len());

    // Every account in a captured set of live mints should walk. A refusal here is not
    // automatically a bug, but it is a change in behaviour and has to be explained rather
    // than noticed later.
    assert_eq!(
        refused, 0,
        "{refused} of {} live mint accounts were refused by the walk",
        names.len()
    );
}

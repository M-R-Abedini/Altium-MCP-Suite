//! Performance regression tests for the write/read and compression paths.
//!
//! Two kinds of check, because a wall-clock number means different things in
//! different builds:
//!
//! - **Scaling** — saving and opening ten times the footprints must cost on
//!   the order of ten times the time, in any build on any machine. This is
//!   what catches an accidental quadratic in the OLE writer or the reader,
//!   which is the regression these tests exist for, and it is independent of
//!   how fast the machine happens to be.
//! - **Absolute bounds** — asserted only in an optimised build, where the
//!   paths run well inside the bound and a miss is a real slowdown. An
//!   unoptimised build on a busy shared runner can take longer than any
//!   bound worth having, so there the timings are printed and not asserted.
//!
//! Timings are the minimum over several runs: the minimum is the closest
//! measurement to the code's own cost, while an average carries whatever else
//! the machine was doing.
//!
//! Run with: `cargo test --release --test perf_tests -- --nocapture`

#![allow(clippy::cast_precision_loss)] // Acceptable for timing display

use std::time::{Duration, Instant};

use altium_designer_mcp::altium::pcblib::{Footprint, Pad, PcbLib};

/// True in an optimised build, where the absolute bounds are asserted.
const OPTIMISED: bool = !cfg!(debug_assertions);

/// Runs `f` `runs` times and returns the fastest run.
fn measure_min<F: FnMut()>(runs: usize, mut f: F) -> Duration {
    (0..runs)
        .map(|_| {
            let start = Instant::now();
            f();
            start.elapsed()
        })
        .min()
        .expect("at least one run")
}

/// Formats a duration in a human-readable way.
fn format_duration(d: Duration) -> String {
    if d.as_nanos() < 1000 {
        format!("{}ns", d.as_nanos())
    } else if d.as_micros() < 1000 {
        format!("{:.2}µs", d.as_nanos() as f64 / 1000.0)
    } else if d.as_millis() < 1000 {
        format!("{:.2}ms", d.as_micros() as f64 / 1000.0)
    } else {
        format!("{:.2}s", d.as_millis() as f64 / 1000.0)
    }
}

/// Builds a `PcbLib` with `n` simple two-pad footprints.
fn build_library(n: usize) -> PcbLib {
    let mut lib = PcbLib::new();
    for i in 0..n {
        let mut fp = Footprint::new(format!("FP_{i}"));
        fp.add_pad(Pad::smd("1", -0.5, 0.0, 0.6, 0.5));
        fp.add_pad(Pad::smd("2", 0.5, 0.0, 0.6, 0.5));
        lib.add(fp);
    }
    lib
}

/// The smaller and larger library sizes the scaling checks compare. The
/// small one is big enough that its timing is the code's, not the timer's:
/// a library that opens in a couple of milliseconds measures scheduler
/// noise, and a noisy denominator makes the ratio meaningless.
const SMALL: usize = 50;
const LARGE: usize = 500;

/// The most the large library may cost relative to the small one. Linear
/// work would give `LARGE / SMALL` = 10; a fixed per-file cost pulls the
/// ratio below that, while a quadratic would push it towards 100. Measured
/// ratios sit between 8 and 14; the ceiling leaves room for a noisy runner
/// and none for a quadratic.
const MAX_SCALING_RATIO: f64 = 30.0;

/// Asserts that `large` cost no more than [`MAX_SCALING_RATIO`] times
/// `small`, printing both so a `--nocapture` run shows the numbers.
fn assert_scales(what: &str, small: Duration, large: Duration) {
    let ratio = large.as_secs_f64() / small.as_secs_f64().max(1e-9);
    println!(
        "{what}: {SMALL} footprints {} / {LARGE} footprints {} (ratio {ratio:.1})",
        format_duration(small),
        format_duration(large)
    );
    assert!(
        ratio <= MAX_SCALING_RATIO,
        "{what} does not scale linearly: {LARGE} footprints cost {ratio:.1}x the {SMALL}-footprint time"
    );
}

/// Asserts `measured < bound` in an optimised build; in a debug build only
/// prints, since the bound would measure the build, not the code.
fn assert_bound(what: &str, measured: Duration, bound: Duration) {
    println!(
        "{what}: {} per op (bound {} — {})",
        format_duration(measured),
        format_duration(bound),
        if OPTIMISED {
            "asserted"
        } else {
            "debug build, not asserted"
        }
    );
    if OPTIMISED {
        assert!(measured < bound, "{what} regressed: {measured:?}");
    }
}

#[test]
fn pcblib_save_scales_linearly_with_footprint_count() {
    let dir = tempfile::tempdir().expect("tempdir");
    let mut small = build_library(SMALL);
    let mut large = build_library(LARGE);
    let small_path = dir.path().join("small.PcbLib");
    let large_path = dir.path().join("large.PcbLib");

    let small_time = measure_min(5, || small.save(&small_path).expect("save"));
    let large_time = measure_min(5, || large.save(&large_path).expect("save"));
    assert_scales("PcbLib save", small_time, large_time);
    assert_bound(
        &format!("PcbLib save ({LARGE} footprints)"),
        large_time,
        Duration::from_secs(1),
    );
}

#[test]
fn pcblib_open_scales_linearly_with_footprint_count() {
    let dir = tempfile::tempdir().expect("tempdir");
    let small_path = dir.path().join("small.PcbLib");
    let large_path = dir.path().join("large.PcbLib");
    build_library(SMALL).save(&small_path).expect("save");
    build_library(LARGE).save(&large_path).expect("save");

    let small_time = measure_min(5, || {
        let _ = PcbLib::open(&small_path).expect("open");
    });
    let large_time = measure_min(5, || {
        let _ = PcbLib::open(&large_path).expect("open");
    });
    assert_scales("PcbLib open", small_time, large_time);
    assert_bound(
        &format!("PcbLib open ({LARGE} footprints)"),
        large_time,
        Duration::from_secs(1),
    );
}

#[test]
fn flate2_roundtrip_1mb_stays_fast() {
    use flate2::read::ZlibDecoder;
    use flate2::write::ZlibEncoder;
    use flate2::Compression;
    use std::io::{Read, Write};

    // STEP models are stored zlib-compressed inside .PcbLib files; this mirrors
    // that compress/decompress path on ~1MB of data.
    let data = vec![0x5Au8; 1024 * 1024];

    let time = measure_min(5, || {
        let mut enc = ZlibEncoder::new(Vec::new(), Compression::default());
        enc.write_all(&data).expect("compress");
        let compressed = enc.finish().expect("finish");

        let mut dec = ZlibDecoder::new(&compressed[..]);
        let mut out = Vec::new();
        dec.read_to_end(&mut out).expect("decompress");
        assert_eq!(out.len(), data.len());
    });
    assert_bound("flate2 1MB round-trip", time, Duration::from_secs(1));
}

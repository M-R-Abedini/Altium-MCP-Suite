//! Shared fixtures for the MCP server and tool handler tests.
//!
//! Compiled only under `cfg(test)`. The `server.rs` test module and each
//! `tools/*.rs` test module draw on these rather than carrying copies of
//! their own, so a fixture is defined once.

use serde_json::Value;
use tempfile::TempDir;

use crate::altium::pcblib::{Footprint, Pad, PcbLib};
use crate::altium::schlib::{Pin, PinOrientation, Rectangle, SchLib, Symbol};
use crate::mcp::server::{McpServer, ToolCallResult, ToolContent};

/// Creates a temporary directory inside `.tmp/` for test isolation.
/// The directory is automatically cleaned up when the returned `TempDir` is dropped.
///
/// Uses an absolute path (canonicalised from the constant `.tmp`, which cargo
/// resolves against the crate root) to avoid issues with parallel test
/// execution. Deriving it from a constant rather than `current_dir()` also
/// avoids the spurious `rust/path-injection` taint `CodeQL` raises on this
/// test-only helper.
pub fn test_temp_dir() -> TempDir {
    std::fs::create_dir_all(".tmp").expect("Failed to create .tmp directory");
    let tmp_root = std::path::Path::new(".tmp")
        .canonicalize()
        .expect("Failed to canonicalise .tmp");
    tempfile::tempdir_in(tmp_root).expect("Failed to create temp dir")
}

/// Helper to create a server with a temp directory as the only allowed path.
pub fn create_test_server(temp_path: &std::path::Path) -> McpServer {
    McpServer::new(vec![temp_path.to_path_buf()])
}

/// Helper to create a test `PcbLib` with two sample footprints.
pub fn create_test_pcblib(path: &std::path::Path) {
    let mut lib = PcbLib::new();

    let mut fp1 = Footprint::new("CHIP_0402");
    fp1.description = "0402 chip resistor".to_string();
    fp1.add_pad(Pad::smd("1", -0.5, 0.0, 0.6, 0.5));
    fp1.add_pad(Pad::smd("2", 0.5, 0.0, 0.6, 0.5));
    lib.add(fp1);

    let mut fp2 = Footprint::new("CHIP_0603");
    fp2.description = "0603 chip resistor".to_string();
    fp2.add_pad(Pad::smd("1", -0.8, 0.0, 0.8, 0.8));
    fp2.add_pad(Pad::smd("2", 0.8, 0.0, 0.8, 0.8));
    lib.add(fp2);

    lib.save(path).expect("Failed to create test PcbLib");
}

/// Helper to create a test `SchLib` with two sample symbols.
pub fn create_test_schlib(path: &std::path::Path) {
    let mut lib = SchLib::new();

    let mut sym1 = Symbol::new("RESISTOR");
    sym1.description = "Generic resistor".to_string();
    sym1.designator = "R?".to_string();
    sym1.add_pin(Pin::new("1", "1", -20, 0, 10, PinOrientation::Left));
    sym1.add_pin(Pin::new("2", "2", 20, 0, 10, PinOrientation::Right));
    sym1.add_rectangle(Rectangle::new(-10, -5, 10, 5));
    lib.add(sym1);

    let mut sym2 = Symbol::new("CAPACITOR");
    sym2.description = "Generic capacitor".to_string();
    sym2.designator = "C?".to_string();
    sym2.add_pin(Pin::new("1", "1", -20, 0, 10, PinOrientation::Left));
    sym2.add_pin(Pin::new("2", "2", 20, 0, 10, PinOrientation::Right));
    lib.add(sym2);

    lib.save(path).expect("Failed to create test SchLib");
}

/// Helper to extract the text payload from a tool result.
pub fn get_result_text(result: &ToolCallResult) -> &str {
    match &result.content[0] {
        ToolContent::Text { text } => text,
    }
}

/// Parses the JSON payload of a tool result, panicking with the raw text on
/// failure so a malformed response is easy to diagnose.
pub fn parse_result_json(result: &ToolCallResult) -> Value {
    let text = get_result_text(result);
    serde_json::from_str(text)
        .unwrap_or_else(|e| panic!("tool result is not valid JSON ({e}): {text}"))
}

// ---- Byte-level comparison of written libraries ----

/// Asserts two optional streams are byte-identical, naming the first
/// divergent offset when they are not.
pub fn assert_same_stream(what: &str, expected: Option<&Vec<u8>>, actual: Option<&Vec<u8>>) {
    let a = expected.map_or(&[][..], Vec::as_slice);
    let b = actual.map_or(&[][..], Vec::as_slice);
    let first = a
        .iter()
        .zip(b)
        .position(|(x, y)| x != y)
        .unwrap_or_else(|| a.len().min(b.len()));
    assert!(
        expected == actual,
        "{what}: expected {} bytes (present: {}), got {} bytes (present: {}), \
             first divergence at {first:#x}",
        a.len(),
        expected.is_some(),
        b.len(),
        actual.is_some()
    );
}

/// Every top-level component storage of an OLE file, keyed by name,
/// with the bytes of the streams that carry the component.
pub fn component_streams(
    path: &std::path::Path,
) -> std::collections::BTreeMap<String, std::collections::BTreeMap<String, Vec<u8>>> {
    let file = std::fs::File::open(path).unwrap();
    let mut cfb = cfb::CompoundFile::open(file).unwrap();
    let streams: Vec<std::path::PathBuf> = cfb
        .walk()
        .filter(cfb::Entry::is_stream)
        .map(|e| e.path().to_path_buf())
        .collect();
    let mut out: std::collections::BTreeMap<_, std::collections::BTreeMap<_, _>> =
        std::collections::BTreeMap::new();
    for stream in streams {
        let mut parts = stream
            .iter()
            .skip(1)
            .map(|p| p.to_string_lossy().into_owned());
        let component = parts.next().unwrap();
        let rest = parts.collect::<Vec<_>>().join("/");
        let library_level = matches!(
            component.as_str(),
            "Library" | "FileVersionInfo" | "FileHeader"
        );
        // A top-level stream (`SchLib`'s image `Storage`, `SectionKeys`)
        // is kept under its own name so it is compared too.
        let rest = if rest.is_empty() {
            ".".to_string()
        } else {
            rest
        };
        let bytes = crate::altium::read_stream_opt(&mut cfb, &stream).unwrap();
        if !library_level {
            out.entry(component).or_default().insert(rest, bytes);
        }
    }
    out
}

/// Every `UniqueID=XXXXXXXX` value in a `SchLib` Data stream.
pub fn unique_ids(bytes: &[u8]) -> Vec<Vec<u8>> {
    const KEY: &[u8] = b"|UniqueID=";
    bytes
        .windows(KEY.len())
        .enumerate()
        .filter(|(_, w)| *w == KEY)
        .filter_map(|(i, _)| bytes.get(i + KEY.len()..i + KEY.len() + 8))
        .map(<[u8]>::to_vec)
        .collect()
}

/// Replaces every `UniqueID` value not in `keep` with `********`.
pub fn mask_generated_ids(bytes: &[u8], keep: &std::collections::HashSet<Vec<u8>>) -> Vec<u8> {
    const KEY: &[u8] = b"|UniqueID=";
    let mut out = bytes.to_vec();
    let mut i = 0;
    while i + KEY.len() + 8 <= out.len() {
        if &out[i..i + KEY.len()] == KEY && !keep.contains(&out[i + KEY.len()..i + KEY.len() + 8]) {
            out[i + KEY.len()..i + KEY.len() + 8].copy_from_slice(b"********");
            i += KEY.len() + 8;
        } else {
            i += 1;
        }
    }
    out
}

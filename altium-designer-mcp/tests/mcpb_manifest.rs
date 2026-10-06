//! Holds `.github/release-assets/mcpb-manifest.json` — the Claude Desktop
//! extension manifest template release.yml stamps and zips into the
//! `.mcpb`/`.dxt` bundle — to the crate it describes, so a rename, a licence
//! change or a CLI change cannot ship a stale extension.

use serde_json::Value;

fn manifest() -> Value {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/.github/release-assets/mcpb-manifest.json"
    );
    let text = std::fs::read_to_string(path).expect("read mcpb-manifest.json");
    serde_json::from_str(&text).expect("mcpb-manifest.json is valid JSON")
}

/// The template mirrors Cargo.toml: same name, description, licence and
/// repository, with the version left as the `@VERSION@` stamp release.yml
/// replaces (the same convention as the release README).
#[test]
fn manifest_mirrors_the_crate() {
    let m = manifest();
    assert_eq!(m["manifest_version"], "0.3");
    assert_eq!(m["name"], env!("CARGO_PKG_NAME"));
    assert_eq!(m["description"], env!("CARGO_PKG_DESCRIPTION"));
    assert_eq!(m["license"], env!("CARGO_PKG_LICENSE"));
    assert_eq!(m["version"], "@VERSION@");
    assert_eq!(
        m["repository"]["url"],
        "https://github.com/embedded-society/altium-designer-mcp"
    );
    for field in ["author", "server", "user_config", "compatibility"] {
        assert!(m.get(field).is_some(), "manifest must carry {field}");
    }
}

/// The server block runs the bundled binary for each platform with the
/// `--allow` grants the directory picker supplies — the exact CLI contract
/// `load_config_with_allow` implements (no config file needed).
#[test]
fn manifest_server_block_matches_the_cli_contract() {
    let m = manifest();
    let server = &m["server"];
    assert_eq!(server["type"], "binary");

    let mcp = &server["mcp_config"];
    assert_eq!(
        mcp["args"],
        serde_json::json!(["--allow", "${user_config.library_paths}"]),
        "args must pass the picked directories straight to --allow"
    );
    assert_eq!(
        mcp["command"], "${__dirname}/server/linux/altium-designer-mcp",
        "the base command is the linux binary; win32/darwin override it"
    );
    assert_eq!(
        mcp["platform_overrides"]["win32"]["command"],
        "${__dirname}/server/win32/altium-designer-mcp.exe"
    );
    assert_eq!(
        mcp["platform_overrides"]["darwin"]["command"],
        "${__dirname}/server/darwin/altium-designer-mcp"
    );

    // The user_config key the args reference must exist, be a directory
    // picker, allow several folders, and refuse to run without one.
    let picker = &m["user_config"]["library_paths"];
    assert_eq!(picker["type"], "directory");
    assert_eq!(picker["multiple"], true);
    assert_eq!(picker["required"], true);
}

/// The manifest's icon is a real square PNG shipped beside it, and its privacy
/// policy link is an HTTPS URL to a README section that exists — what a
/// directory listing requires of a local connector.
#[test]
fn manifest_icon_and_privacy_policy_are_real() {
    let m = manifest();
    assert_eq!(m["icon"], "icon.png");
    let icon = std::fs::read(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/.github/release-assets/icon.png"
    ))
    .expect("icon.png ships beside the manifest");
    assert_eq!(&icon[..8], b"\x89PNG\r\n\x1a\n", "icon.png is a PNG");
    let (width, height) = (
        u32::from_be_bytes([icon[16], icon[17], icon[18], icon[19]]),
        u32::from_be_bytes([icon[20], icon[21], icon[22], icon[23]]),
    );
    assert_eq!(width, height, "the icon is square");
    assert!(width >= 256, "the icon is at least 256 px, got {width}");

    let policies = m["privacy_policies"].as_array().expect("privacy_policies");
    assert_eq!(policies.len(), 1);
    let url = policies[0].as_str().expect("a URL");
    assert!(url.starts_with("https://"), "{url}");
    assert!(url.ends_with("README.md#privacy-policy"), "{url}");
    let readme = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/README.md"))
        .expect("README")
        .replace("\r\n", "\n");
    assert!(
        readme.contains("\n## Privacy Policy\n"),
        "README carries the Privacy Policy section the manifest links to"
    );
}

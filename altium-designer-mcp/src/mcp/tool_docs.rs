//! Generates `docs/TOOLS.md` from [`McpServer::get_tool_definitions`], so the
//! human-readable tool reference cannot drift from the schema the server
//! actually serves over `tools/list`.
//!
//! The committed `docs/TOOLS.md` is a build artifact of the tool definitions:
//! a unit test ([`tests::tools_md_in_sync`]) fails if the file is out of date,
//! and `UPDATE_DOCS=1 cargo test --lib tools_md_in_sync` regenerates it. Tool
//! descriptions, parameter schemas, and the per-tool `example` all live in
//! `tool_definitions.rs` (the single source of truth); nothing here is authored
//! by hand.

use std::fmt::Write as _;

use serde_json::Value;

use crate::mcp::server::McpServer;

/// Path to the generated doc, relative to the crate manifest dir.
const TOOLS_MD_REL: &str = "docs/TOOLS.md";

const HEADER: &str = "<!-- GENERATED — do not edit by hand.
     Source of truth: src/mcp/tool_definitions.rs
     Regenerate: UPDATE_DOCS=1 cargo test --lib tools_md_in_sync -->

<!-- markdownlint-disable MD013 -->
<!-- Generated tables and inline JSON schemas legitimately exceed the line-length limit. -->

# MCP Tools Reference

Every tool the **altium-designer-mcp** server exposes, rendered from the tool
definitions served over `tools/list`. Coordinates are millimetres for `.PcbLib`
footprints and schematic units (10 units = 1 grid square) for `.SchLib` symbols.
";

/// Maximum prose width for the generated document (STYLE § General Rules).
const MAX_PROSE_WIDTH: usize = 170;

/// Renders the full Markdown reference for every registered tool.
pub fn render_tools_markdown() -> String {
    let mut out = String::from(HEADER);
    let tools = McpServer::get_tool_definitions();
    let _ = writeln!(out, "\n_{} tools._", tools.len());

    for tool in &tools {
        let _ = writeln!(out, "\n## `{}`", tool.name);
        if let Some(annotations) = &tool.annotations {
            let _ = writeln!(
                out,
                "\n**{}** — {}",
                annotations.title,
                if annotations.read_only_hint {
                    "read-only"
                } else {
                    "writes files (a backup is taken first)"
                }
            );
        }
        if let Some(desc) = &tool.description {
            let _ = writeln!(out, "\n{}", wrap_prose(desc.trim()));
        }
        if let Some(example) = &tool.example {
            out.push_str("\n**Example**\n\n```json\n");
            out.push_str(&to_json_pretty_4(example));
            out.push_str("\n```\n");
        }
        out.push_str(&render_params(&tool.input_schema));
    }
    out
}

/// Serialises `value` as pretty-printed JSON with 4-space indentation, so the
/// generated examples comply with STYLE § JSON (the `serde_json` default is
/// 2 spaces).
fn to_json_pretty_4(value: &Value) -> String {
    use serde::Serialize as _;

    let mut buf = Vec::new();
    let formatter = serde_json::ser::PrettyFormatter::with_indent(b"    ");
    let mut ser = serde_json::Serializer::with_formatter(&mut buf, formatter);
    if value.serialize(&mut ser).is_err() {
        return String::new();
    }
    String::from_utf8(buf).unwrap_or_default()
}

/// Greedy word-wrap for prose paragraphs at [`MAX_PROSE_WIDTH`] columns,
/// preserving the author's existing line breaks (STYLE § General Rules caps
/// lines at 170 characters).
fn wrap_prose(text: &str) -> String {
    let mut out = String::new();
    for (i, line) in text.lines().enumerate() {
        if i > 0 {
            out.push('\n');
        }
        if line.chars().count() <= MAX_PROSE_WIDTH {
            out.push_str(line);
            continue;
        }
        let mut column = 0;
        for word in line.split_whitespace() {
            let len = word.chars().count();
            if column == 0 {
                out.push_str(word);
                column = len;
            } else if column + 1 + len <= MAX_PROSE_WIDTH {
                out.push(' ');
                out.push_str(word);
                column += 1 + len;
            } else {
                out.push('\n');
                out.push_str(word);
                column = len;
            }
        }
    }
    out
}

/// Renders the parameter table for one tool's input schema.
fn render_params(schema: &Value) -> String {
    let props = schema.get("properties").and_then(Value::as_object);
    let Some(props) = props else {
        return "\n_No parameters._\n".to_string();
    };
    let required: Vec<&str> = schema
        .get("required")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(Value::as_str).collect())
        .unwrap_or_default();

    // Sort property names so the table is deterministic regardless of the
    // serde_json map backing (BTreeMap vs preserve_order IndexMap).
    let mut names: Vec<&String> = props.keys().collect();
    names.sort();

    let mut out = String::from(
        "\n**Parameters**\n\n| Name | Type | Required | Description |\n| --- | --- | --- | --- |\n",
    );
    for name in names {
        let p = &props[name];
        let req = if required.contains(&name.as_str()) {
            "yes"
        } else {
            "no"
        };
        let _ = writeln!(
            out,
            "| `{}` | {} | {} | {} |",
            name,
            schema_type(p),
            req,
            describe(p)
        );
    }
    out
}

/// Human-readable type for a schema property (`string`, `array<object>`, …).
fn schema_type(p: &Value) -> String {
    if p.get("enum").is_some() {
        return "enum".to_string();
    }
    match p.get("type").and_then(Value::as_str) {
        Some("array") => {
            let item = p
                .get("items")
                .and_then(|i| i.get("type"))
                .and_then(Value::as_str)
                .unwrap_or("any");
            format!("array<{item}>")
        }
        Some(t) => t.to_string(),
        None => "any".to_string(),
    }
}

/// Description cell: the schema `description`, with enum values and any default
/// appended so the table is self-contained.
fn describe(p: &Value) -> String {
    let mut s = p
        .get("description")
        .and_then(Value::as_str)
        .unwrap_or("")
        .replace('\n', " ")
        .replace('|', "\\|");
    if let Some(vals) = p.get("enum").and_then(Value::as_array) {
        let joined = vals
            .iter()
            .filter_map(Value::as_str)
            .collect::<Vec<_>>()
            .join(", ");
        if !joined.is_empty() {
            let _ = write!(s, " (one of: {joined})");
        }
    }
    match (p.get("minimum"), p.get("maximum")) {
        (Some(min), Some(max)) => {
            let _ = write!(s, " (range: {min}-{max})");
        }
        (Some(min), None) => {
            let _ = write!(s, " (min: {min})");
        }
        (None, Some(max)) => {
            let _ = write!(s, " (max: {max})");
        }
        (None, None) => {}
    }
    if let Some(def) = p.get("default") {
        let _ = write!(s, " (default: `{def}`)");
    }
    s.trim().to_string()
}

#[cfg(test)]
mod tests {
    use super::{render_tools_markdown, TOOLS_MD_REL};
    use crate::mcp::server::McpServer;
    use serde_json::Value;

    /// The rendering helpers, which only ever run against the committed tool
    /// definitions and so never meet the shapes a future schema could bring.
    mod rendering {
        use super::super::{describe, render_params, schema_type, wrap_prose, MAX_PROSE_WIDTH};
        use serde_json::json;

        #[test]
        fn prose_wraps_long_lines_and_keeps_the_authors_own_breaks() {
            // The generated doc has a line-length cap, but an author's
            // deliberate paragraph break carries meaning and must survive.
            let authored = "short line\nanother short line";
            assert_eq!(wrap_prose(authored), authored);

            let long = "word ".repeat(80);
            let wrapped = wrap_prose(long.trim());
            assert!(wrapped.contains('\n'), "a long line should have wrapped");
            for line in wrapped.lines() {
                assert!(
                    line.chars().count() <= MAX_PROSE_WIDTH,
                    "line over the cap: {line:?}"
                );
            }

            // A single word longer than the cap cannot be broken, so it stands
            // alone rather than being truncated.
            let unbreakable = "x".repeat(MAX_PROSE_WIDTH + 20);
            assert_eq!(wrap_prose(&unbreakable), unbreakable);
        }

        #[test]
        fn a_schema_with_no_properties_says_so_rather_than_rendering_an_empty_table() {
            assert!(render_params(&json!({})).contains("_No parameters._"));
            assert!(render_params(&json!({ "type": "object" })).contains("_No parameters._"));
        }

        /// A range the schema states is part of the description cell, in
        /// whichever half it gives.
        #[test]
        fn a_range_is_rendered_beside_the_description() {
            assert_eq!(
                describe(&json!({ "description": "Font.", "minimum": 1, "maximum": 255 })),
                "Font. (range: 1-255)"
            );
            assert_eq!(describe(&json!({ "minimum": -1 })), "(min: -1)");
            assert_eq!(describe(&json!({ "maximum": 34 })), "(max: 34)");
            assert_eq!(describe(&json!({ "description": "Plain." })), "Plain.");
        }

        #[test]
        fn an_untyped_property_renders_as_any() {
            // A property with no `type` is legal JSON Schema; the table has to
            // say something rather than omit the cell.
            assert_eq!(schema_type(&json!({})), "any");
            assert_eq!(schema_type(&json!({ "type": "string" })), "string");
            assert_eq!(schema_type(&json!({ "type": "array" })), "array<any>");
            assert_eq!(
                schema_type(&json!({ "type": "array", "items": { "type": "number" } })),
                "array<number>"
            );
        }
    }

    /// Every per-tool `example` must be a valid call for that tool: it must name
    /// the right tool, use only documented top-level arguments (the same
    /// contract the strict-deserialization allow-lists enforce at runtime), and
    /// supply every required argument. A stale or hand-typo'd example would
    /// otherwise ship in docs/TOOLS.md and mislead an agent.
    #[test]
    fn examples_are_schema_valid() {
        let mut problems: Vec<String> = Vec::new();
        for tool in McpServer::get_tool_definitions() {
            let Some(example) = &tool.example else {
                continue;
            };
            let t = &tool.name;
            if example.get("name").and_then(Value::as_str) != Some(t.as_str()) {
                problems.push(format!(
                    "{t}: example names the wrong tool (or omits `name`)"
                ));
            }
            let Some(args) = example.get("arguments").and_then(Value::as_object) else {
                problems.push(format!("{t}: example has no `arguments` object"));
                continue;
            };
            let Some(props) = tool
                .input_schema
                .get("properties")
                .and_then(Value::as_object)
            else {
                continue;
            };
            for key in args.keys() {
                if !props.contains_key(key) {
                    problems.push(format!("{t}: argument `{key}` is not in the input schema"));
                }
            }
            if let Some(required) = tool.input_schema.get("required").and_then(Value::as_array) {
                for req in required.iter().filter_map(Value::as_str) {
                    if !args.contains_key(req) {
                        problems.push(format!("{t}: missing required argument `{req}`"));
                    }
                }
            }
        }
        assert!(
            problems.is_empty(),
            "tool examples disagree with their schemas:\n  {}",
            problems.join("\n  ")
        );
    }

    /// The internal `example` must never leak onto the `tools/list` wire — it is
    /// not part of the MCP tool schema. Guards the `#[serde(skip)]`.
    #[test]
    fn example_field_is_not_serialized() {
        let tool = McpServer::get_tool_definitions()
            .into_iter()
            .find(|t| t.example.is_some())
            .expect("at least one tool carries an example");
        let wire = serde_json::to_value(&tool).expect("serialize ToolDefinition");
        assert!(
            wire.get("example").is_none(),
            "`example` must be #[serde(skip)] — it leaked into tools/list output"
        );
        assert!(
            wire.get("inputSchema").is_some(),
            "the real schema is still serialized (camelCase)"
        );
    }

    /// Guards the hand-written tool index in `README.md` and every "N tools"
    /// count in the docs against `tool_definitions.rs`: the index links
    /// exactly the tools that exist, and each stated count is the real one.
    /// Neither is generated, so this is what keeps them honest.
    #[test]
    fn readme_tool_index_and_tool_counts_in_sync() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
        let tools: std::collections::BTreeSet<String> = McpServer::get_tool_definitions()
            .into_iter()
            .map(|t| t.name)
            .collect();

        let readme = std::fs::read_to_string(root.join("README.md")).expect("read README.md");
        let linked: std::collections::BTreeSet<String> = readme
            .match_indices("](docs/TOOLS.md#")
            .map(|(i, _)| {
                let rest = &readme[i + "](docs/TOOLS.md#".len()..];
                rest[..rest.find(')').expect("closing paren")].to_string()
            })
            .collect();
        assert_eq!(
            linked, tools,
            "README.md's tool index must link exactly the tools that exist"
        );

        for doc in [
            "README.md",
            "docs/CLIENT_SETUP.md",
            "docs/USAGE.md",
            "docs/TOOLS.md",
            ".github/release-assets/README.md",
        ] {
            let text = std::fs::read_to_string(root.join(doc)).expect(doc);
            for (i, _) in text.match_indices(" tools") {
                let before: String = text[..i]
                    .chars()
                    .rev()
                    .take_while(char::is_ascii_digit)
                    .collect();
                if before.is_empty() {
                    continue;
                }
                let stated: usize = before.chars().rev().collect::<String>().parse().unwrap();
                // Cursor's cap across all servers, quoted in CLIENT_SETUP.md,
                // is the one count that is not ours.
                if stated == 100 {
                    continue;
                }
                assert_eq!(stated, tools.len(), "{doc} states {stated} tools");
            }
        }
    }

    /// Guards `docs/TOOLS.md` against drift from `tool_definitions.rs`. If a
    /// tool's schema, description, or example changes and the doc isn't
    /// regenerated, this fails. Regenerate with:
    ///   `UPDATE_DOCS=1 cargo test --lib tools_md_in_sync`
    #[test]
    fn tools_md_in_sync() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join(TOOLS_MD_REL);
        let generated = render_tools_markdown();

        if std::env::var_os("UPDATE_DOCS").is_some() {
            std::fs::write(&path, &generated).expect("write docs/TOOLS.md");
            return;
        }

        let committed = std::fs::read_to_string(&path)
            .unwrap_or_default()
            .replace("\r\n", "\n");
        assert_eq!(
            generated.replace("\r\n", "\n"),
            committed,
            "docs/TOOLS.md is out of date with tool_definitions.rs. \
             Regenerate: UPDATE_DOCS=1 cargo test --lib tools_md_in_sync"
        );
    }
}

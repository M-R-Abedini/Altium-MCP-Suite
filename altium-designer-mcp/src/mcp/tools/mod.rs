//! Per-domain MCP tool handlers and helpers, split out of `server.rs`.
//!
//! Each submodule adds an `impl McpServer` block. Method resolution is
//! independent of which file an `impl` lives in, so the dispatch in `server.rs`
//! (and the in-crate tests) call these methods unchanged via `Self::`/`self.`.
//! Helpers reached across modules are `pub(crate)`.

mod allowed_keys;
mod batch;
mod compare;
mod components;
mod diff;
mod library_ops;
mod maintenance;
#[cfg(test)]
mod mutation_fidelity;
mod parsing;
/// The primitive kinds `update_primitive` addresses, shared by its handler,
/// its schema and the guard test.
pub(super) use maintenance::UPDATE_PRIMITIVE_KINDS;
/// The accepted values of every enum-valued field, shared by the parsers
/// that read them and the tool schemas that advertise them.
pub(super) use parsing::accepted;
mod query_update;
mod read_write;
mod render;
mod schlib_manage;
mod step;
#[cfg(test)]
pub mod test_support;
mod validation;

/// The error for a component a tool was asked for and the library does not
/// hold: the request as made, then what is there — the first ten names and
/// a count of the rest — in the same words from every tool that looks one
/// up.
pub fn component_not_found(component_name: &str, names: &[String]) -> String {
    component_not_found_in(component_name, "library", names)
}

/// [`component_not_found`] for a tool that holds more than one library,
/// where `which` says which one was searched ("source library", a file
/// name the caller passed).
pub fn component_not_found_in(component_name: &str, which: &str, names: &[String]) -> String {
    const SHOWN: usize = 10;
    let available = if names.is_empty() {
        "none (the library is empty)".to_string()
    } else {
        let shown = names
            .iter()
            .take(SHOWN)
            .map(String::as_str)
            .collect::<Vec<_>>()
            .join(", ");
        match names.len().saturating_sub(SHOWN) {
            0 => shown,
            rest => format!("{shown} ... and {rest} more"),
        }
    };
    format!("Component '{component_name}' not found in {which}. Available: {available}")
}

/// The `limit` / `offset` pair every paging tool takes: `limit` a whole
/// number of 1 or more (absent = all), `offset` a whole number of 0 or more
/// (absent = 0). Anything else is refused by name — `0` would page forever
/// and a negative used to read as absent.
pub fn page_arguments(arguments: &serde_json::Value) -> Result<(Option<usize>, usize), String> {
    use serde_json::Value;

    let whole = |value: &Value| value.as_u64().and_then(|n| usize::try_from(n).ok());
    let limit =
        match arguments.get("limit") {
            None | Some(Value::Null) => None,
            Some(value) => Some(whole(value).filter(|n| *n >= 1).ok_or_else(|| {
                format!("limit must be a whole number of 1 or more, got {value}")
            })?),
        };
    let offset = match arguments.get("offset") {
        None | Some(Value::Null) => 0,
        Some(value) => whole(value)
            .ok_or_else(|| format!("offset must be a whole number of 0 or more, got {value}"))?,
    };
    Ok((limit, offset))
}

/// The error for a path that is neither a `.PcbLib` nor a `.SchLib`: the
/// extension the caller gave (or its absence), the file's name and the two
/// kinds accepted, in the same words from every tool that opens a library.
pub fn unsupported_file_type(filepath: &str) -> String {
    let path = std::path::Path::new(filepath);
    let name = crate::altium::error::sanitise_path_for_client(path);
    path.extension().and_then(|e| e.to_str()).map_or_else(
        || format!("'{name}' has no file extension: expected .PcbLib or .SchLib"),
        |ext| format!("Unsupported file type '.{ext}' for '{name}': expected .PcbLib or .SchLib"),
    )
}

#[cfg(test)]
mod tests {
    use super::{
        component_not_found, component_not_found_in, page_arguments, unsupported_file_type,
    };
    use serde_json::json;

    /// Absent means all / from the start; a zero or negative limit, a
    /// negative offset and a fraction are refused by name.
    #[test]
    fn page_arguments_take_a_positive_limit_and_a_non_negative_offset() {
        assert_eq!(page_arguments(&json!({})), Ok((None, 0)));
        assert_eq!(
            page_arguments(&json!({ "limit": null, "offset": null })),
            Ok((None, 0))
        );
        assert_eq!(
            page_arguments(&json!({ "limit": 3, "offset": 1 })),
            Ok((Some(3), 1))
        );
        for (arguments, expected) in [
            (
                json!({ "limit": 0 }),
                "limit must be a whole number of 1 or more, got 0",
            ),
            (
                json!({ "limit": -1 }),
                "limit must be a whole number of 1 or more, got -1",
            ),
            (
                json!({ "limit": 2.5 }),
                "limit must be a whole number of 1 or more, got 2.5",
            ),
            (
                json!({ "offset": -1 }),
                "offset must be a whole number of 0 or more, got -1",
            ),
        ] {
            assert_eq!(page_arguments(&arguments), Err(expected.to_string()));
        }
    }

    /// The message names the request, then the first ten names on file and
    /// how many more there are; an empty library says so.
    #[test]
    fn a_missing_component_is_reported_with_what_is_there() {
        let names = |n: usize| (1..=n).map(|i| format!("C{i}")).collect::<Vec<_>>();
        assert_eq!(
            component_not_found("X", &names(0)),
            "Component 'X' not found in library. Available: none (the library is empty)"
        );
        assert_eq!(
            component_not_found("X", &names(2)),
            "Component 'X' not found in library. Available: C1, C2"
        );
        assert_eq!(
            component_not_found("X", &names(10)),
            "Component 'X' not found in library. Available: C1, C2, C3, C4, C5, C6, C7, C8, C9, C10"
        );
        assert_eq!(
            component_not_found("x", &names(12)),
            "Component 'x' not found in library. Available: C1, C2, C3, C4, C5, C6, C7, C8, C9, C10 \
             ... and 2 more"
        );
        assert_eq!(
            component_not_found_in("X", "source library", &names(1)),
            "Component 'X' not found in source library. Available: C1"
        );
    }

    /// The message names the extension given, or its absence, and only the
    /// file's name — never its directory.
    #[test]
    fn an_unsupported_file_type_is_named_with_the_file_alone() {
        assert_eq!(
            unsupported_file_type("C:/secret/dir/Parts.csv"),
            "Unsupported file type '.csv' for 'Parts.csv': expected .PcbLib or .SchLib"
        );
        assert_eq!(
            unsupported_file_type("/srv/libs/Parts"),
            "'Parts' has no file extension: expected .PcbLib or .SchLib"
        );
    }
}

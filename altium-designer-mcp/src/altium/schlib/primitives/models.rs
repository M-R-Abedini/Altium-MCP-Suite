//! `SchLib` footprint model references.

#[allow(clippy::wildcard_imports)] // sibling primitive types
use super::*;

/// A footprint model reference.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FootprintModel {
    /// Model name (footprint name in `PcbLib`).
    pub name: String,
    /// Description.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub description: String,
    /// Path to the `PcbLib` that contains this footprint, written as
    /// `ModelDatafile0`. When set, Altium resolves the footprint directly from
    /// that file (rendering the preview); when absent it falls back to searching
    /// available libraries by name, which reports "footprint not found" if the
    /// library isn't installed/in the project.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub library_path: Option<String>,
    /// Whether this is the current/default footprint model (`IsCurrent=T`).
    /// Preserved on read; on write the first model is still emitted as current
    /// (positional), so this is read-preserved only until multi-model authoring lands.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub is_current: bool,
    /// The record's `UniqueID`, preserved on read so a read-modify-write
    /// re-emits the same id; a from-scratch model generates a fresh one.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub unique_id: Option<String>,
    /// The `RECORD=45` exactly as read (see `raw_params` on every graphic):
    /// a UI-authored link carries `IntegratedModel=T|DatabaseModel=T` and
    /// omits an empty `Description`, all of which come back as stored.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub raw_params: Vec<(String, String)>,
}

impl FootprintModel {
    /// Creates a new footprint model reference.
    #[must_use]
    pub fn new(name: impl Into<String>) -> Self {
        Self {
            raw_params: Vec::new(),
            name: name.into(),
            description: String::new(),
            library_path: None,
            is_current: false,
            unique_id: None,
        }
    }
}

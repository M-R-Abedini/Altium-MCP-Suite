//! `PcbLib` copper/mechanical layer enum and its Altium layer-ID mapping.

use serde::{Deserialize, Serialize};

/// Declares [`Layer`] and everything that must list every layer — the
/// Altium name, the camel-case alias the JSON boundary also accepts, and
/// [`Layer::ALL`] — from ONE list, so a layer cannot be missing from a table.
macro_rules! layers {
    (
        $(#[$enum_doc:meta])*
        $enum_name:ident {
            $( $(#[$meta:meta])* $variant:ident => $name:literal | $alias:literal ),+ $(,)?
        }
    ) => {
        $(#[$enum_doc])*
        #[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
        pub enum $enum_name {
            $( $(#[$meta])* #[serde(rename = $name, alias = $alias)] $variant, )+
        }

        impl $enum_name {
            /// Every layer, in declaration order.
            pub const ALL: [Self; [$(stringify!($variant)),+].len()] = [$(Self::$variant,)+];

            /// Returns the Altium layer name string.
            #[must_use]
            pub const fn as_str(&self) -> &'static str {
                match self {
                    $(Self::$variant => $name,)+
                }
            }

            /// The camel-case alias (`TopOverlay` for `Top Overlay`), the
            /// spelling the JSON boundary accepts besides the Altium name.
            #[must_use]
            pub const fn alias(&self) -> &'static str {
                match self {
                    $(Self::$variant => $alias,)+
                }
            }
        }
    };
}

layers! {
    /// Altium layer identifiers.
    ///
    /// # Recommended Layers for Footprints
    ///
    /// AI assistants should prefer these dedicated layers over generic mechanical layers:
    ///
    /// | Purpose | Recommended Layer |
    /// |---------|-------------------|
    /// | Pads (SMD) | `TopLayer` or `BottomLayer` |
    /// | Pads (through-hole) | `MultiLayer` |
    /// | Silkscreen | `TopOverlay` / `BottomOverlay` |
    /// | Assembly outline | `TopAssembly` / `BottomAssembly` |
    /// | Courtyard | `TopCourtyard` / `BottomCourtyard` |
    /// | 3D body outline | `Top3DBody` / `Bottom3DBody` |
    Layer {
        // Copper layers
        /// Top copper layer (ID 1).
        TopLayer => "Top Layer" | "TopLayer",
        /// Mid layer 1 (ID 2).
        MidLayer1 => "Mid-Layer 1" | "MidLayer1",
        /// Mid layer 2 (ID 3).
        MidLayer2 => "Mid-Layer 2" | "MidLayer2",
        /// Mid layer 3 (ID 4).
        MidLayer3 => "Mid-Layer 3" | "MidLayer3",
        /// Mid layer 4 (ID 5).
        MidLayer4 => "Mid-Layer 4" | "MidLayer4",
        /// Mid layer 5 (ID 6).
        MidLayer5 => "Mid-Layer 5" | "MidLayer5",
        /// Mid layer 6 (ID 7).
        MidLayer6 => "Mid-Layer 6" | "MidLayer6",
        /// Mid layer 7 (ID 8).
        MidLayer7 => "Mid-Layer 7" | "MidLayer7",
        /// Mid layer 8 (ID 9).
        MidLayer8 => "Mid-Layer 8" | "MidLayer8",
        /// Mid layer 9 (ID 10).
        MidLayer9 => "Mid-Layer 9" | "MidLayer9",
        /// Mid layer 10 (ID 11).
        MidLayer10 => "Mid-Layer 10" | "MidLayer10",
        /// Mid layer 11 (ID 12).
        MidLayer11 => "Mid-Layer 11" | "MidLayer11",
        /// Mid layer 12 (ID 13).
        MidLayer12 => "Mid-Layer 12" | "MidLayer12",
        /// Mid layer 13 (ID 14).
        MidLayer13 => "Mid-Layer 13" | "MidLayer13",
        /// Mid layer 14 (ID 15).
        MidLayer14 => "Mid-Layer 14" | "MidLayer14",
        /// Mid layer 15 (ID 16).
        MidLayer15 => "Mid-Layer 15" | "MidLayer15",
        /// Mid layer 16 (ID 17).
        MidLayer16 => "Mid-Layer 16" | "MidLayer16",
        /// Mid layer 17 (ID 18).
        MidLayer17 => "Mid-Layer 17" | "MidLayer17",
        /// Mid layer 18 (ID 19).
        MidLayer18 => "Mid-Layer 18" | "MidLayer18",
        /// Mid layer 19 (ID 20).
        MidLayer19 => "Mid-Layer 19" | "MidLayer19",
        /// Mid layer 20 (ID 21).
        MidLayer20 => "Mid-Layer 20" | "MidLayer20",
        /// Mid layer 21 (ID 22).
        MidLayer21 => "Mid-Layer 21" | "MidLayer21",
        /// Mid layer 22 (ID 23).
        MidLayer22 => "Mid-Layer 22" | "MidLayer22",
        /// Mid layer 23 (ID 24).
        MidLayer23 => "Mid-Layer 23" | "MidLayer23",
        /// Mid layer 24 (ID 25).
        MidLayer24 => "Mid-Layer 24" | "MidLayer24",
        /// Mid layer 25 (ID 26).
        MidLayer25 => "Mid-Layer 25" | "MidLayer25",
        /// Mid layer 26 (ID 27).
        MidLayer26 => "Mid-Layer 26" | "MidLayer26",
        /// Mid layer 27 (ID 28).
        MidLayer27 => "Mid-Layer 27" | "MidLayer27",
        /// Mid layer 28 (ID 29).
        MidLayer28 => "Mid-Layer 28" | "MidLayer28",
        /// Mid layer 29 (ID 30).
        MidLayer29 => "Mid-Layer 29" | "MidLayer29",
        /// Mid layer 30 (ID 31).
        MidLayer30 => "Mid-Layer 30" | "MidLayer30",
        /// Bottom copper layer (ID 32).
        BottomLayer => "Bottom Layer" | "BottomLayer",
        /// Multi-layer (all copper layers, for through-hole pads).
        #[default]
        MultiLayer => "Multi-Layer" | "MultiLayer",
        // Silkscreen
        /// Top silkscreen (overlay).
        TopOverlay => "Top Overlay" | "TopOverlay",
        /// Bottom silkscreen.
        BottomOverlay => "Bottom Overlay" | "BottomOverlay",
        // Solder mask
        /// Top solder mask.
        TopSolder => "Top Solder" | "TopSolder",
        /// Bottom solder mask.
        BottomSolder => "Bottom Solder" | "BottomSolder",
        // Internal planes (IDs 39-54)
        /// Internal plane 1 (ID 39).
        InternalPlane1 => "Internal Plane 1" | "InternalPlane1",
        /// Internal plane 2 (ID 40).
        InternalPlane2 => "Internal Plane 2" | "InternalPlane2",
        /// Internal plane 3 (ID 41).
        InternalPlane3 => "Internal Plane 3" | "InternalPlane3",
        /// Internal plane 4 (ID 42).
        InternalPlane4 => "Internal Plane 4" | "InternalPlane4",
        /// Internal plane 5 (ID 43).
        InternalPlane5 => "Internal Plane 5" | "InternalPlane5",
        /// Internal plane 6 (ID 44).
        InternalPlane6 => "Internal Plane 6" | "InternalPlane6",
        /// Internal plane 7 (ID 45).
        InternalPlane7 => "Internal Plane 7" | "InternalPlane7",
        /// Internal plane 8 (ID 46).
        InternalPlane8 => "Internal Plane 8" | "InternalPlane8",
        /// Internal plane 9 (ID 47).
        InternalPlane9 => "Internal Plane 9" | "InternalPlane9",
        /// Internal plane 10 (ID 48).
        InternalPlane10 => "Internal Plane 10" | "InternalPlane10",
        /// Internal plane 11 (ID 49).
        InternalPlane11 => "Internal Plane 11" | "InternalPlane11",
        /// Internal plane 12 (ID 50).
        InternalPlane12 => "Internal Plane 12" | "InternalPlane12",
        /// Internal plane 13 (ID 51).
        InternalPlane13 => "Internal Plane 13" | "InternalPlane13",
        /// Internal plane 14 (ID 52).
        InternalPlane14 => "Internal Plane 14" | "InternalPlane14",
        /// Internal plane 15 (ID 53).
        InternalPlane15 => "Internal Plane 15" | "InternalPlane15",
        /// Internal plane 16 (ID 54).
        InternalPlane16 => "Internal Plane 16" | "InternalPlane16",
        // Drill layers
        /// Drill guide layer (ID 55).
        DrillGuide => "Drill Guide" | "DrillGuide",
        /// Drill drawing layer (ID 73).
        DrillDrawing => "Drill Drawing" | "DrillDrawing",
        // Paste
        /// Top solder paste.
        TopPaste => "Top Paste" | "TopPaste",
        /// Bottom solder paste.
        BottomPaste => "Bottom Paste" | "BottomPaste",
        // Component layer pairs (preferred over generic mechanical layers)
        /// Top assembly outline (component body outline for documentation).
        TopAssembly => "Top Assembly" | "TopAssembly",
        /// Bottom assembly outline.
        BottomAssembly => "Bottom Assembly" | "BottomAssembly",
        /// Top courtyard (component keepout area per IPC-7351).
        TopCourtyard => "Top Courtyard" | "TopCourtyard",
        /// Bottom courtyard.
        BottomCourtyard => "Bottom Courtyard" | "BottomCourtyard",
        /// Top 3D body outline (for 3D model placement).
        Top3DBody => "Top 3D Body" | "Top3DBody",
        /// Bottom 3D body outline.
        Bottom3DBody => "Bottom 3D Body" | "Bottom3DBody",
        // Generic mechanical layers (use component layer pairs when possible)
        /// Mechanical layer 1 (ID 57).
        Mechanical1 => "Mechanical 1" | "Mechanical1",
        /// Mechanical layer 2 (ID 58 - aliased to `TopAssembly`).
        Mechanical2 => "Mechanical 2" | "Mechanical2",
        /// Mechanical layer 3 (ID 59 - aliased to `BottomAssembly`).
        Mechanical3 => "Mechanical 3" | "Mechanical3",
        /// Mechanical layer 4 (ID 60 - aliased to `TopCourtyard`).
        Mechanical4 => "Mechanical 4" | "Mechanical4",
        /// Mechanical layer 5 (ID 61 - aliased to `BottomCourtyard`).
        Mechanical5 => "Mechanical 5" | "Mechanical5",
        /// Mechanical layer 6 (ID 62 - aliased to `Top3DBody`).
        Mechanical6 => "Mechanical 6" | "Mechanical6",
        /// Mechanical layer 7 (ID 63 - aliased to `Bottom3DBody`).
        Mechanical7 => "Mechanical 7" | "Mechanical7",
        /// Mechanical layer 8 (ID 64).
        Mechanical8 => "Mechanical 8" | "Mechanical8",
        /// Mechanical layer 9 (ID 65).
        Mechanical9 => "Mechanical 9" | "Mechanical9",
        /// Mechanical layer 10 (ID 66).
        Mechanical10 => "Mechanical 10" | "Mechanical10",
        /// Mechanical layer 11 (ID 67).
        Mechanical11 => "Mechanical 11" | "Mechanical11",
        /// Mechanical layer 12 (ID 68).
        Mechanical12 => "Mechanical 12" | "Mechanical12",
        /// Mechanical layer 13 (ID 69).
        Mechanical13 => "Mechanical 13" | "Mechanical13",
        /// Mechanical layer 14 (ID 70).
        Mechanical14 => "Mechanical 14" | "Mechanical14",
        /// Mechanical layer 15 (ID 71).
        Mechanical15 => "Mechanical 15" | "Mechanical15",
        /// Mechanical layer 16 (ID 72).
        Mechanical16 => "Mechanical 16" | "Mechanical16",
        // Extended mechanical layers (IDs 186-201, Altium Designer 18+)
        /// Mechanical layer 17 (ID 186).
        Mechanical17 => "Mechanical 17" | "Mechanical17",
        /// Mechanical layer 18 (ID 187).
        Mechanical18 => "Mechanical 18" | "Mechanical18",
        /// Mechanical layer 19 (ID 188).
        Mechanical19 => "Mechanical 19" | "Mechanical19",
        /// Mechanical layer 20 (ID 189).
        Mechanical20 => "Mechanical 20" | "Mechanical20",
        /// Mechanical layer 21 (ID 190).
        Mechanical21 => "Mechanical 21" | "Mechanical21",
        /// Mechanical layer 22 (ID 191).
        Mechanical22 => "Mechanical 22" | "Mechanical22",
        /// Mechanical layer 23 (ID 192).
        Mechanical23 => "Mechanical 23" | "Mechanical23",
        /// Mechanical layer 24 (ID 193).
        Mechanical24 => "Mechanical 24" | "Mechanical24",
        /// Mechanical layer 25 (ID 194).
        Mechanical25 => "Mechanical 25" | "Mechanical25",
        /// Mechanical layer 26 (ID 195).
        Mechanical26 => "Mechanical 26" | "Mechanical26",
        /// Mechanical layer 27 (ID 196).
        Mechanical27 => "Mechanical 27" | "Mechanical27",
        /// Mechanical layer 28 (ID 197).
        Mechanical28 => "Mechanical 28" | "Mechanical28",
        /// Mechanical layer 29 (ID 198).
        Mechanical29 => "Mechanical 29" | "Mechanical29",
        /// Mechanical layer 30 (ID 199).
        Mechanical30 => "Mechanical 30" | "Mechanical30",
        /// Mechanical layer 31 (ID 200).
        Mechanical31 => "Mechanical 31" | "Mechanical31",
        /// Mechanical layer 32 (ID 201).
        Mechanical32 => "Mechanical 32" | "Mechanical32",
        // Special layers (IDs 75-85)
        /// Connect layer (ID 75).
        ConnectLayer => "Connect Layer" | "ConnectLayer",
        /// Background layer (ID 76).
        BackgroundLayer => "Background Layer" | "BackgroundLayer",
        /// DRC error layer (ID 77).
        DRCErrorLayer => "DRC Error Layer" | "DRCErrorLayer",
        /// Highlight layer (ID 78).
        HighlightLayer => "Highlight Layer" | "HighlightLayer",
        /// Grid color 1 layer (ID 79).
        GridColor1 => "Grid Color 1" | "GridColor1",
        /// Grid color 10 layer (ID 80).
        GridColor10 => "Grid Color 10" | "GridColor10",
        /// Pad hole layer (ID 81).
        PadHoleLayer => "Pad Hole Layer" | "PadHoleLayer",
        /// Via hole layer (ID 82).
        ViaHoleLayer => "Via Hole Layer" | "ViaHoleLayer",
        /// Top pad master layer (ID 83).
        TopPadMaster => "Top Pad Master" | "TopPadMaster",
        /// Bottom pad master layer (ID 84).
        BottomPadMaster => "Bottom Pad Master" | "BottomPadMaster",
        /// DRC detail layer (ID 85).
        DRCDetailLayer => "DRC Detail Layer" | "DRCDetailLayer",
        // Keep-out
        /// Keep-out layer (ID 56).
        KeepOut => "Keep-Out Layer" | "KeepOut",
    }
}

impl Layer {
    /// Parses a layer from its Altium name (`Top Overlay`) or camel-case
    /// alias (`TopOverlay`). Case and the spaces, hyphens and underscores
    /// between words do not matter, so `top overlay`, `TOPOVERLAY` and
    /// `mid-layer 3` parse too. Every tool that takes a layer name resolves
    /// it here, so they all accept the same spellings.
    #[must_use]
    pub fn parse(s: &str) -> Option<Self> {
        let wanted = fold_layer_name(s);
        Self::ALL.into_iter().find(|layer| {
            fold_layer_name(layer.as_str()) == wanted || fold_layer_name(layer.alias()) == wanted
        })
    }
}

/// A layer name without case, spaces, hyphens or underscores, so spellings
/// that differ only in those compare equal.
fn fold_layer_name(s: &str) -> String {
    s.chars()
        .filter(|c| !matches!(c, ' ' | '-' | '_'))
        .flat_map(char::to_lowercase)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::Layer;

    const ALL: &[Layer] = &Layer::ALL;

    #[test]
    fn every_variant_round_trips_through_as_str_and_parse() {
        for layer in ALL {
            let name = layer.as_str();
            assert_eq!(
                Layer::parse(name),
                Some(*layer),
                "'{name}' did not round-trip"
            );
            // Names are never empty and carry no leading/trailing whitespace.
            assert!(!name.is_empty());
            assert_eq!(name.trim(), name);
        }
    }

    /// Every spelling a caller may reach for resolves to the same layer: the
    /// camel-case alias, the serde form, and the name in any case with or
    /// without its separators.
    #[test]
    fn every_variant_parses_from_its_alias_and_folded_spellings() {
        for layer in ALL {
            let name = layer.as_str();
            let alias = layer.alias();
            assert_eq!(Layer::parse(alias), Some(*layer), "alias '{alias}'");
            assert_eq!(
                serde_json::from_value::<Layer>(serde_json::Value::String(alias.to_string())).ok(),
                Some(*layer),
                "serde alias '{alias}'"
            );
            for spelling in [
                name.to_lowercase(),
                name.to_uppercase(),
                name.replace(' ', "_"),
                name.replace([' ', '-'], ""),
            ] {
                assert_eq!(Layer::parse(&spelling), Some(*layer), "'{spelling}'");
            }
        }
    }

    #[test]
    fn all_layer_names_are_unique() {
        let mut names: Vec<&str> = ALL.iter().map(Layer::as_str).collect();
        names.sort_unstable();
        let count = names.len();
        names.dedup();
        assert_eq!(names.len(), count, "duplicate layer name string");
    }

    #[test]
    fn parse_rejects_unknown_names() {
        assert_eq!(Layer::parse(""), None);
        assert_eq!(Layer::parse("Not A Real Layer"), None);
        // A family prefix without its number, or past the family, is no layer.
        assert_eq!(Layer::parse("MidLayer"), None);
        assert_eq!(Layer::parse("Mechanical 33"), None);
        assert_eq!(Layer::parse("Top"), None);
    }

    #[test]
    fn default_is_a_known_layer() {
        // The derived Default must be one of the enumerated variants.
        let d = Layer::default();
        assert!(ALL.contains(&d));
        assert_eq!(Layer::parse(d.as_str()), Some(d));
    }
}

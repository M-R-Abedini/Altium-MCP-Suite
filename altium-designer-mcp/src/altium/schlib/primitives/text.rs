//! `SchLib` text primitives (labels, text annotations, parameters).

#[allow(clippy::wildcard_imports)] // sibling primitive types
use super::*;

/// A text label.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Label {
    /// X position.
    #[serde(serialize_with = "crate::altium::serde_round::serialize")]
    pub x: f64,
    /// Y position.
    #[serde(serialize_with = "crate::altium::serde_round::serialize")]
    pub y: f64,
    /// Text content.
    pub text: String,
    /// Font ID (1-based index into library fonts).
    #[serde(default = "default_font_id")]
    pub font_id: u8,
    /// Text colour (BGR format).
    #[serde(default)]
    pub color: u32,
    /// Text justification.
    #[serde(default = "default_justification")]
    pub justification: TextJustification,
    /// Rotation in degrees.
    #[serde(default, serialize_with = "crate::altium::serde_round::serialize")]
    pub rotation: f64,
    /// Whether the label is mirrored horizontally.
    #[serde(default)]
    pub is_mirrored: bool,
    /// Whether the label is hidden.
    #[serde(default)]
    pub is_hidden: bool,
    /// Owner part ID.
    #[serde(default = "default_owner_part")]
    pub owner_part_id: i32,
    /// Universal display/lock flags; omitted from JSON when all default.
    #[serde(default, flatten)]
    pub display_flags: ShapeDisplayFlags,
    /// Altium unique ID (8-char). Preserved on read so a round-trip keeps the
    /// shape identity; a from-scratch shape generates a fresh one on write (#113).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub unique_id: Option<String>,
    /// The record exactly as read: every `key=value` segment in stored order
    /// (an empty segment as `("", "")`), so the writer replays it verbatim
    /// where the field behind a segment is unchanged and emits only the keys
    /// Altium wrote — the UI omits `LineWidth=1` on a rectangle, a script
    /// does not. Empty for a record built from scratch, which emits the
    /// canonical form.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub raw_params: Vec<(String, String)>,
}

impl Label {
    /// Creates a label reading `text` at (`x`, `y`) with Altium's defaults:
    /// font 1, black, bottom-left anchored, unrotated, visible.
    #[must_use]
    pub fn new(x: impl Into<f64>, y: impl Into<f64>, text: impl Into<String>) -> Self {
        Self {
            x: x.into(),
            y: y.into(),
            text: text.into(),
            font_id: 1,
            color: 0,
            justification: TextJustification::BottomLeft,
            rotation: 0.0,
            is_mirrored: false,
            is_hidden: false,
            owner_part_id: 1,
            display_flags: ShapeDisplayFlags::default(),
            unique_id: None,
            raw_params: Vec::new(),
        }
    }
}

/// A bordered multi-line text box — `SchLib` `RECORD=28`.
///
/// Distinct from [`Label`]: the text lives inside a frame rectangle
/// (Location + Corner) with its own border, fill and text colours, a text
/// margin, word-wrap and clip-to-rect behaviour. Coordinates are `f64`
/// schematic units (see `super::coord`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[allow(clippy::struct_excessive_bools)] // independent Altium record flags
pub struct TextFrame {
    /// First corner X (`Location.X`).
    #[serde(serialize_with = "crate::altium::serde_round::serialize")]
    pub x1: f64,
    /// First corner Y (`Location.Y`).
    #[serde(serialize_with = "crate::altium::serde_round::serialize")]
    pub y1: f64,
    /// Second corner X (`Corner.X`).
    #[serde(serialize_with = "crate::altium::serde_round::serialize")]
    pub x2: f64,
    /// Second corner Y (`Corner.Y`).
    #[serde(serialize_with = "crate::altium::serde_round::serialize")]
    pub y2: f64,
    /// Text content (`TEXT`).
    pub text: String,
    /// Border colour (BGR format, `COLOR`). Omitted when zero (default).
    #[serde(default)]
    pub color: u32,
    /// Fill colour (BGR format, `AREACOLOR`). Defaults to white (0xFFFFFF),
    /// which Altium emits even on a from-scratch frame.
    #[serde(default = "default_area_color")]
    pub area_color: u32,
    /// Text colour (BGR format, `TEXTCOLOR`). Omitted when zero (default).
    #[serde(default)]
    pub text_color: u32,
    /// Margin between the frame border and the text (`TEXTMARGIN`), a
    /// fractional coordinate. Altium's from-scratch default is 0.00005 units
    /// (the record carries only `TextMargin_Frac=5`).
    #[serde(
        default = "default_text_margin",
        serialize_with = "crate::altium::serde_round::serialize"
    )]
    pub text_margin: f64,
    /// Border line width (`LINEWIDTH`). Omitted when zero (default).
    #[serde(default)]
    pub line_width: u8,
    /// Border line style (`LINESTYLE`, 0 = Solid). Omitted when zero.
    #[serde(default)]
    pub line_style: u8,
    /// Whether the fill is transparent. Emitted only when true.
    #[serde(default)]
    pub transparent: bool,
    /// Font ID (1-based index into library fonts).
    #[serde(default = "default_font_id")]
    pub font_id: u8,
    /// Text orientation (`ORIENTATION`): `0`/`1`/`2`/`3` = 0°/90°/180°/270°.
    /// Omitted when zero (default).
    #[serde(default)]
    pub orientation: u8,
    /// Text alignment (`ALIGNMENT`): 0 = left, 1 = centre, 2 = right. Default 1
    /// (centre) — Altium emits `Alignment=1` even on a from-scratch frame.
    #[serde(default = "default_alignment")]
    pub alignment: u8,
    /// Whether the frame is filled (`ISSOLID`). Emitted only when true.
    #[serde(default)]
    pub is_solid: bool,
    /// Whether the border is shown (`SHOWBORDER`). Default true; emitted only
    /// when true.
    #[serde(default = "default_true")]
    pub show_border: bool,
    /// Whether the text word-wraps inside the frame (`WORDWRAP`). Default true;
    /// emitted only when true.
    #[serde(default = "default_true")]
    pub word_wrap: bool,
    /// Whether the text is clipped to the frame rectangle (`CLIPTORECT`).
    /// Default true; emitted only when true.
    #[serde(default = "default_true")]
    pub clip_to_rect: bool,
    /// Whether the frame is marked not-accessible. Altium tags every shape
    /// `IsNotAccesible` (its own single-'s' spelling), so this defaults to true.
    #[serde(default = "default_true")]
    pub is_not_accessible: bool,
    /// Owner part ID.
    #[serde(default = "default_owner_part")]
    pub owner_part_id: i32,
    /// Universal display/lock flags; omitted from JSON when all default.
    #[serde(default, flatten)]
    pub display_flags: ShapeDisplayFlags,
    /// Altium unique ID (8-char). Preserved on read so a round-trip keeps the
    /// shape identity; a from-scratch shape generates a fresh one on write (#113).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub unique_id: Option<String>,
    /// The record exactly as read: every `key=value` segment in stored order
    /// (an empty segment as `("", "")`), so the writer replays it verbatim
    /// where the field behind a segment is unchanged and emits only the keys
    /// Altium wrote — the UI omits `LineWidth=1` on a rectangle, a script
    /// does not. Empty for a record built from scratch, which emits the
    /// canonical form.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub raw_params: Vec<(String, String)>,
}

impl TextFrame {
    /// Creates a new text frame spanning the box `(x1,y1)`-`(x2,y2)` holding
    /// `text`, with Altium's from-scratch defaults (white fill, centre
    /// alignment, border shown, word-wrap and clip-to-rect on).
    #[must_use]
    pub fn new(
        x1: impl Into<f64>,
        y1: impl Into<f64>,
        x2: impl Into<f64>,
        y2: impl Into<f64>,
        text: impl Into<String>,
    ) -> Self {
        Self {
            raw_params: Vec::new(),
            x1: x1.into(),
            y1: y1.into(),
            x2: x2.into(),
            y2: y2.into(),
            text: text.into(),
            color: 0,
            area_color: default_area_color(),
            text_color: 0,
            text_margin: default_text_margin(),
            line_width: 0,
            line_style: 0,
            transparent: false,
            font_id: 1,
            orientation: 0,
            alignment: 1,
            is_solid: false,
            show_border: true,
            word_wrap: true,
            clip_to_rect: true,
            is_not_accessible: true,
            owner_part_id: 1,
            display_flags: ShapeDisplayFlags::default(),
            unique_id: None,
        }
    }
}

const fn default_true_param() -> bool {
    true
}

const fn default_font_id() -> u8 {
    1
}

/// A text frame's default fill colour: white (BGR 0xFFFFFF). Altium emits
/// `AreaColor=16777215` even on a from-scratch frame.
const fn default_area_color() -> u32 {
    16_777_215
}

/// A text frame's default alignment: 1 = centre. Altium emits `Alignment=1`
/// even on a from-scratch frame.
const fn default_alignment() -> u8 {
    1
}

/// A text frame's default text margin: 0.00005 schematic units — Altium's
/// from-scratch record carries only `TextMargin_Frac=5`.
const fn default_text_margin() -> f64 {
    0.000_05
}

/// Text justification. Shared with `PcbLib`; the canonical definition is
/// [`crate::altium::TextJustification`].
pub use crate::altium::TextJustification;

/// `SchLib`'s per-field default justification. Unlike `PcbLib` (which defaults
/// to `MiddleCenter`, the shared enum's `Default`), `SchLib` text/labels default
/// to `BottomLeft`, so the `justification` fields set this explicitly.
const fn default_justification() -> TextJustification {
    TextJustification::BottomLeft
}

/// A component parameter (e.g., Value, Part Number).
///
/// Coordinates are `f64` schematic units (see `super::coord`); `Eq` is not
/// derived (floats are only `PartialEq`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[allow(clippy::struct_excessive_bools)] // independent Altium display/visibility toggles
pub struct Parameter {
    /// Parameter name (e.g., "Value", "Part Number").
    pub name: String,
    /// Parameter value (e.g., "10k", "*").
    #[serde(default)]
    pub value: String,
    /// X position.
    #[serde(default, serialize_with = "crate::altium::serde_round::serialize")]
    pub x: f64,
    /// Y position.
    #[serde(default, serialize_with = "crate::altium::serde_round::serialize")]
    pub y: f64,
    /// Font ID.
    #[serde(default = "default_font_id")]
    pub font_id: u8,
    /// Text colour (BGR format).
    #[serde(default)]
    pub color: u32,
    /// Whether the parameter is hidden.
    #[serde(default)]
    pub hidden: bool,
    /// Read-only state (0 = editable, 1 = read-only).
    #[serde(default)]
    pub read_only_state: u8,
    /// Parameter type (0 = String, 1 = Boolean, 2 = Integer, 3 = Float).
    #[serde(default)]
    pub param_type: u8,
    /// Text orientation (`ORIENTATION`): `0`/`1`/`2`/`3` = 0°/90°/180°/270°.
    /// Omit-when-default: the reader defaults an absent key to `0` and the writer
    /// emits `Orientation` only when non-zero. Default `0`.
    #[serde(default, skip_serializing_if = "is_zero_i32")]
    pub orientation: i32,
    /// Text justification (`JUSTIFICATION`): the Altium anchor id 0–8
    /// (0 = bottom-left … 4 = middle-centre … 8 = top-right). Omit-when-default:
    /// the golden's user parameters carry `Justification=8`/`=4` and omit the key
    /// at 0, so the reader defaults an absent key to `0` and the writer emits
    /// `Justification` only when non-zero. Default `0`.
    #[serde(default, skip_serializing_if = "is_zero_u8")]
    pub justification: u8,
    /// Whether to show the parameter name alongside its value (`SHOWNAME`).
    /// Omit-when-default: Altium omits the key for a from-scratch parameter (the
    /// golden's visible + hidden parameters carry neither `SHOWNAME` nor
    /// `HIDENAME`), so the reader defaults an absent key to `false` and the writer
    /// emits `ShowName=T` only when set. Default `false`.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub show_name: bool,
    /// Whether to hide the parameter name, showing only the value (`HIDENAME`).
    /// Omit-when-default (see `show_name`). Default `false`.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub hide_name: bool,
    /// Whether the parameter text is mirrored (`IsMirrored`). AD24 writes the
    /// key at the very end of the record, after `UniqueID` — unlike a label,
    /// which carries it before. Omit-when-default. Default `false`.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub is_mirrored: bool,
    /// Parameter description text (`DESCRIPTION`). Omit-when-default: emitted only
    /// when non-empty. Default empty.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub description: String,
    /// Whether the parameter is variant-configurable (`ISCONFIGURABLE`).
    /// Omit-when-default: emitted only when `true`. Default `false`.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub is_configurable: bool,
    /// Whether Altium auto-positions the parameter label relative to the component.
    ///
    /// Stored inverted and omit-when-default as `NotAutoPosition=T`: an authored
    /// library omits the key while auto-positioning is ON and writes it only when the
    /// user turns it off. Default `true`.
    #[serde(default = "default_true_param", skip_serializing_if = "Clone::clone")]
    pub auto_position: bool,
    /// Whether the parameter carries a PCB design-rule directive (`ISRULE`).
    /// Omit-when-default: emitted only when `true`. Default `false`.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub is_rule: bool,
    /// Whether this is a system parameter rather than a user one
    /// (`ISSYSTEMPARAMETER`). Omit-when-default: emitted only when `true`.
    /// Default `false`.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub is_system_parameter: bool,
    /// Horizontal text-box anchor (`TEXTHORZANCHOR`), distinct from
    /// [`Self::justification`]. Omit-when-default: emitted only when non-zero.
    /// Default `0`.
    #[serde(default, skip_serializing_if = "is_zero_u8")]
    pub text_horz_anchor: u8,
    /// Vertical text-box anchor (`TEXTVERTANCHOR`). Omit-when-default: emitted only
    /// when non-zero. Default `0`.
    #[serde(default, skip_serializing_if = "is_zero_u8")]
    pub text_vert_anchor: u8,
    /// Owner part ID.
    #[serde(default = "default_owner_part")]
    pub owner_part_id: i32,
    /// Universal display/lock flags; omitted from JSON when all default.
    #[serde(default, flatten)]
    pub display_flags: ShapeDisplayFlags,
    /// Altium unique ID (8-char). Preserved on read so a round-trip keeps the
    /// parameter identity; a from-scratch parameter generates a fresh one on write.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub unique_id: Option<String>,
    /// The record exactly as read: every `key=value` segment in stored order
    /// (an empty segment as `("", "")`), so the writer replays it verbatim
    /// where the field behind a segment is unchanged and emits only the keys
    /// Altium wrote — the UI omits `LineWidth=1` on a rectangle, a script
    /// does not. Empty for a record built from scratch, which emits the
    /// canonical form.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub raw_params: Vec<(String, String)>,
}

impl Parameter {
    /// Creates a new parameter.
    #[must_use]
    pub fn new(name: impl Into<String>, value: impl Into<String>) -> Self {
        Self {
            raw_params: Vec::new(),
            name: name.into(),
            value: value.into(),
            x: 0.0,
            y: 0.0,
            font_id: 1,
            color: 0x80_00_00, // Dark blue (BGR)
            hidden: false,
            read_only_state: 0,
            param_type: 0,
            orientation: 0,
            justification: 0,
            show_name: false,
            is_mirrored: false,
            hide_name: false,
            description: String::new(),
            is_configurable: false,
            auto_position: true,
            is_rule: false,
            is_system_parameter: false,
            text_horz_anchor: 0,
            text_vert_anchor: 0,
            owner_part_id: 1,
            display_flags: ShapeDisplayFlags::default(),
            unique_id: None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{Parameter, TextFrame};

    // ==================== serde defaults =====================================
    //
    // These fire when a caller's JSON omits the field, which is the normal
    // case: the writer omits anything at its default, so a read-modify-write
    // round-trip relies on the value coming back on the way in. A wrong
    // default is silent — the record deserialises fine and writes different
    // bytes than it was read with.

    #[test]
    fn an_omitted_label_field_takes_altiums_own_default() {
        use super::{Label, TextJustification};

        let label: Label = serde_json::from_str(r#"{"x":0,"y":0,"text":"L"}"#)
            .expect("a minimal label should deserialise");
        // Font id 0 is not a font; Altium's first is 1.
        assert_eq!(label.font_id, 1);
        // SchLib anchors bottom-left, unlike the shared enum's MiddleCenter —
        // defaulting to the shared value would shift every label on save.
        assert_eq!(label.justification, TextJustification::BottomLeft);
    }

    #[test]
    fn an_omitted_text_frame_field_takes_the_from_scratch_record_value() {
        let frame: TextFrame = serde_json::from_str(r#"{"x1":0,"y1":0,"x2":10,"y2":5,"text":"F"}"#)
            .expect("a minimal text frame should deserialise");

        assert_eq!(frame.font_id, 1);
        // Altium writes these on a from-scratch frame, so omitting them must
        // reproduce them rather than emit black, left-aligned and zero-margin.
        assert_eq!(frame.area_color, 16_777_215, "white");
        assert_eq!(frame.alignment, 1, "centre");
        assert!((frame.text_margin - 0.000_05).abs() < 1e-12);
    }

    #[test]
    fn an_omitted_parameter_flag_defaults_to_auto_positioned() {
        let param: Parameter = serde_json::from_str(r#"{"name":"Value","value":"1k"}"#)
            .expect("a minimal parameter should deserialise");
        assert_eq!(param.font_id, 1);
        // Stored inverted as `NotAutoPosition=T`, so an authored library omits
        // the key while auto-positioning is on. Defaulting to `false` here
        // would make every round-tripped parameter gain that key.
        assert!(param.auto_position);
    }

    #[test]
    fn text_frame_new_stores_geometry_text_and_defaults() {
        let tf = TextFrame::new(-10, -5, 10, 5, "hello");
        assert_eq!(tf.text, "hello");
        assert!((tf.x1 - -10.0).abs() < 1e-9);
        assert!((tf.y2 - 5.0).abs() < 1e-9);
        // Documented defaults.
        assert!(tf.show_border);
        assert!(tf.word_wrap);
        assert!(tf.clip_to_rect);
        assert_eq!(tf.owner_part_id, 1);
    }

    #[test]
    fn parameter_new_stores_name_value_and_defaults() {
        let p = Parameter::new("Value", "10k");
        assert_eq!(p.name, "Value");
        assert_eq!(p.value, "10k");
        assert!(!p.hidden);
        assert_eq!(p.owner_part_id, 1);
    }
}

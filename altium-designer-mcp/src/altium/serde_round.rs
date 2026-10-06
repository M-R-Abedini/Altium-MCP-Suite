//! Shared serde helper that rounds `f64` values to 6 decimal places on
//! serialization.
//!
//! Both `PcbLib` (mm coordinates) and `SchLib` (rotation/angle fields) round
//! float JSON output identically; this is the single implementation they share
//! rather than duplicate `coord_serde`/`float_serde` modules. The
//! rounding is identical for both, so serialized output matches.

use serde::{Deserialize, Deserializer, Serialize, Serializer};

/// Rounds a value to 6 decimal places.
#[inline]
fn round(value: f64) -> f64 {
    (value * 1_000_000.0).round() / 1_000_000.0
}

/// Serialises an f64 with rounding.
#[allow(clippy::trivially_copy_pass_by_ref)] // serde requires &T signature
pub fn serialize<S: Serializer>(value: &f64, serializer: S) -> Result<S::Ok, S::Error> {
    serializer.serialize_f64(round(*value))
}

/// Serialises an optional f64 with rounding.
pub mod option {
    use super::{round, Deserialize, Deserializer, Serializer};

    #[allow(clippy::ref_option)] // serde requires &Option<T> signature
    pub fn serialize<S: Serializer>(value: &Option<f64>, serializer: S) -> Result<S::Ok, S::Error> {
        match value {
            Some(v) => serializer.serialize_some(&round(*v)),
            None => serializer.serialize_none(),
        }
    }

    pub fn deserialize<'de, D: Deserializer<'de>>(
        deserializer: D,
    ) -> Result<Option<f64>, D::Error> {
        Option::<f64>::deserialize(deserializer)
    }
}

/// A coordinate pair in any spelling the tools accept: the `{x, y}` and
/// `{width, height}` objects the tool schemas document, or a bare `[a, b]`
/// pair.
#[derive(Deserialize)]
#[serde(untagged)]
enum Pair {
    Xy { x: f64, y: f64 },
    Wh { width: f64, height: f64 },
    Seq(f64, f64),
}

impl From<Pair> for (f64, f64) {
    fn from(pair: Pair) -> Self {
        match pair {
            Pair::Xy { x, y } => (x, y),
            Pair::Wh { width, height } => (width, height),
            Pair::Seq(a, b) => (a, b),
        }
    }
}

/// Reads one coordinate pair from JSON in any accepted spelling (see
/// [`Pair`]); `None` when the value is none of them.
pub fn pair_from_json(value: &serde_json::Value) -> Option<(f64, f64)> {
    serde_json::from_value::<Pair>(value.clone())
        .ok()
        .map(Into::into)
}

/// The rounded `{x, y}` object a pair serialises to.
#[derive(Serialize)]
struct Xy {
    x: f64,
    y: f64,
}

/// The rounded `{width, height}` object a size pair serialises to.
#[derive(Serialize)]
struct Wh {
    width: f64,
    height: f64,
}

/// Serialises a `Vec` of (x, y) pairs as rounded `{x, y}` objects — the
/// shape the tool schemas document — and reads back any [`Pair`] spelling.
pub mod xy_points {
    use super::{round, Deserialize, Deserializer, Pair, Serialize, Serializer, Xy};

    pub fn serialize<S: Serializer>(
        value: &[(f64, f64)],
        serializer: S,
    ) -> Result<S::Ok, S::Error> {
        let points: Vec<Xy> = value
            .iter()
            .map(|&(x, y)| Xy {
                x: round(x),
                y: round(y),
            })
            .collect();
        points.serialize(serializer)
    }

    pub fn deserialize<'de, D: Deserializer<'de>>(
        deserializer: D,
    ) -> Result<Vec<(f64, f64)>, D::Error> {
        Ok(Vec::<Pair>::deserialize(deserializer)?
            .into_iter()
            .map(Into::into)
            .collect())
    }
}

/// [`xy_points`] for an optional list.
pub mod xy_points_opt {
    use super::{xy_points, Deserialize, Deserializer, Serializer};

    #[allow(clippy::ref_option)] // serde requires &Option<T> signature
    pub fn serialize<S: Serializer>(
        value: &Option<Vec<(f64, f64)>>,
        serializer: S,
    ) -> Result<S::Ok, S::Error> {
        match value {
            Some(v) => xy_points::serialize(v, serializer),
            None => serializer.serialize_none(),
        }
    }

    pub fn deserialize<'de, D: Deserializer<'de>>(
        deserializer: D,
    ) -> Result<Option<Vec<(f64, f64)>>, D::Error> {
        #[derive(Deserialize)]
        struct Wrap(#[serde(with = "xy_points")] Vec<(f64, f64)>);
        Ok(Option::<Wrap>::deserialize(deserializer)?.map(|w| w.0))
    }
}

/// Serialises an optional `Vec` of (width, height) pairs as rounded
/// `{width, height}` objects and reads back any [`Pair`] spelling.
pub mod wh_pairs_opt {
    use super::{round, Deserialize, Deserializer, Pair, Serialize, Serializer, Wh};

    #[allow(clippy::ref_option)] // serde requires &Option<T> signature
    pub fn serialize<S: Serializer>(
        value: &Option<Vec<(f64, f64)>>,
        serializer: S,
    ) -> Result<S::Ok, S::Error> {
        match value {
            Some(v) => {
                let sizes: Vec<Wh> = v
                    .iter()
                    .map(|&(width, height)| Wh {
                        width: round(width),
                        height: round(height),
                    })
                    .collect();
                sizes.serialize(serializer)
            }
            None => serializer.serialize_none(),
        }
    }

    pub fn deserialize<'de, D: Deserializer<'de>>(
        deserializer: D,
    ) -> Result<Option<Vec<(f64, f64)>>, D::Error> {
        Ok(Option::<Vec<Pair>>::deserialize(deserializer)?
            .map(|pairs| pairs.into_iter().map(Into::into).collect()))
    }
}

/// Serialises a `Vec` of f64 with rounding.
pub mod vec_f64 {
    use super::{round, Deserialize, Deserializer, Serialize, Serializer};

    #[allow(clippy::ref_option)] // serde requires &Option<T> signature
    pub fn serialize<S: Serializer>(
        value: &Option<Vec<f64>>,
        serializer: S,
    ) -> Result<S::Ok, S::Error> {
        match value {
            Some(v) => {
                let rounded: Vec<f64> = v.iter().map(|x| round(*x)).collect();
                rounded.serialize(serializer)
            }
            None => serializer.serialize_none(),
        }
    }

    pub fn deserialize<'de, D: Deserializer<'de>>(
        deserializer: D,
    ) -> Result<Option<Vec<f64>>, D::Error> {
        Option::<Vec<f64>>::deserialize(deserializer)
    }
}

#[cfg(test)]
mod tests {
    use serde::{Deserialize, Serialize};
    use serde_json::{json, Value};

    // Test structs wiring each helper into serde via the same attributes the
    // real primitives use, so the tests exercise the actual serialise path.
    #[derive(Serialize)]
    struct Bare {
        #[serde(serialize_with = "super::serialize")]
        v: f64,
    }

    #[derive(Serialize, Deserialize, PartialEq, Debug)]
    struct Opt {
        #[serde(with = "super::option")]
        v: Option<f64>,
    }

    #[derive(Serialize, Deserialize, PartialEq, Debug)]
    struct Points {
        #[serde(with = "super::xy_points")]
        v: Vec<(f64, f64)>,
    }

    #[derive(Serialize, Deserialize, PartialEq, Debug)]
    struct PointsOpt {
        #[serde(with = "super::xy_points_opt")]
        v: Option<Vec<(f64, f64)>>,
    }

    #[derive(Serialize, Deserialize, PartialEq, Debug)]
    struct Sizes {
        #[serde(with = "super::wh_pairs_opt")]
        v: Option<Vec<(f64, f64)>>,
    }

    #[derive(Serialize, Deserialize, PartialEq, Debug)]
    struct VecF {
        #[serde(with = "super::vec_f64")]
        v: Option<Vec<f64>>,
    }

    #[test]
    fn round_reduces_to_six_decimal_places() {
        assert!((super::round(1.234_567_89) - 1.234_568).abs() < 1e-12);
        assert!((super::round(9.999_999_9) - 10.0).abs() < 1e-12);
        // A value already within 6 dp is unchanged.
        assert!((super::round(-2.5) + 2.5).abs() < 1e-12);
    }

    #[test]
    fn bare_serialiser_rounds() {
        let j = serde_json::to_value(Bare { v: 1.234_567_89 }).unwrap();
        assert_eq!(j["v"], json!(1.234_568));
    }

    #[test]
    fn option_serialises_some_rounded_and_none_null() {
        let some = serde_json::to_value(Opt {
            v: Some(0.123_456_789),
        })
        .unwrap();
        assert_eq!(some["v"], json!(0.123_457));
        let none = serde_json::to_value(Opt { v: None }).unwrap();
        assert_eq!(none["v"], Value::Null);
    }

    #[test]
    fn option_round_trips_both_variants() {
        for v in [Some(1.5_f64), None] {
            let s = serde_json::to_string(&Opt { v }).unwrap();
            let back: Opt = serde_json::from_str(&s).unwrap();
            assert_eq!(back.v, v);
        }
    }

    #[test]
    fn xy_points_serialise_as_rounded_objects() {
        let j = serde_json::to_value(Points {
            v: vec![(1.111_111_9, -2.222_222_1)],
        })
        .unwrap();
        assert_eq!(j["v"], json!([{ "x": 1.111_112, "y": -2.222_222 }]));
    }

    #[test]
    fn xy_points_read_every_accepted_spelling() {
        let back: Points = serde_json::from_value(json!({
            "v": [{ "x": 1.0, "y": 2.0 }, [3.0, 4.0], { "width": 5.0, "height": 6.0 }]
        }))
        .unwrap();
        assert_eq!(back.v, vec![(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)]);
        assert!(serde_json::from_value::<Points>(json!({ "v": [{ "x": 1.0 }] })).is_err());
        assert!(serde_json::from_value::<Points>(json!({ "v": [[1.0]] })).is_err());
    }

    #[test]
    fn xy_points_opt_serialises_some_as_objects_and_none_null() {
        let some = serde_json::to_value(PointsOpt {
            v: Some(vec![(1.5, 2.5)]),
        })
        .unwrap();
        assert_eq!(some["v"], json!([{ "x": 1.5, "y": 2.5 }]));
        let none = serde_json::to_value(PointsOpt { v: None }).unwrap();
        assert_eq!(none["v"], Value::Null);
        let back: PointsOpt = serde_json::from_value(json!({ "v": [[1.0, 2.0]] })).unwrap();
        assert_eq!(back.v, Some(vec![(1.0, 2.0)]));
        let absent: PointsOpt = serde_json::from_value(json!({ "v": null })).unwrap();
        assert_eq!(absent.v, None);
    }

    #[test]
    fn wh_pairs_opt_serialise_as_sizes_and_read_every_spelling() {
        let some = serde_json::to_value(Sizes {
            v: Some(vec![(1.111_111_9, 2.0)]),
        })
        .unwrap();
        assert_eq!(some["v"], json!([{ "width": 1.111_112, "height": 2.0 }]));
        let none = serde_json::to_value(Sizes { v: None }).unwrap();
        assert_eq!(none["v"], Value::Null);
        let back: Sizes = serde_json::from_value(json!({
            "v": [{ "width": 1.0, "height": 2.0 }, [3.0, 4.0], { "x": 5.0, "y": 6.0 }]
        }))
        .unwrap();
        assert_eq!(back.v, Some(vec![(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)]));
    }

    #[test]
    fn pair_from_json_reads_every_spelling_and_rejects_the_rest() {
        assert_eq!(
            super::pair_from_json(&json!({ "x": 1.0, "y": 2.0 })),
            Some((1.0, 2.0))
        );
        assert_eq!(
            super::pair_from_json(&json!({ "width": 3.0, "height": 4.0 })),
            Some((3.0, 4.0))
        );
        assert_eq!(super::pair_from_json(&json!([5, 6])), Some((5.0, 6.0)));
        assert_eq!(super::pair_from_json(&json!({ "x": 1.0 })), None);
        assert_eq!(super::pair_from_json(&json!([1.0])), None);
        assert_eq!(super::pair_from_json(&json!("1,2")), None);
    }

    #[test]
    fn vec_f64_serialises_rounded_and_none_null() {
        let some = serde_json::to_value(VecF {
            v: Some(vec![9.999_999_9, 0.000_000_4]),
        })
        .unwrap();
        assert_eq!(some["v"], json!([10.0, 0.0]));
        let none = serde_json::to_value(VecF { v: None }).unwrap();
        assert_eq!(none["v"], Value::Null);
    }

    #[test]
    fn vec_f64_round_trips() {
        let orig = VecF {
            v: Some(vec![1.0, 2.0, 3.0]),
        };
        let back: VecF = serde_json::from_str(&serde_json::to_string(&orig).unwrap()).unwrap();
        assert_eq!(back, orig);
    }
}

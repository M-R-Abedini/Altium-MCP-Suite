#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Bradley-Terry trainer: pairwise preferences -> quality model.

Reads the JSONL log produced by the vote UI (or the
design_record_preference MCP tool), fits feature weights so that the
predicted preferences match observed ones, writes
``quality_model.json``. The pipeline's ``score_canvas`` then loads
those weights and uses them in place of the hand-tuned heuristic.

Model:

    s(canvas) = sum(w_i * feature_i(canvas))

    P(A beats B) = sigmoid(s(A) - s(B))

Loss is logistic on observed preferences (tie weighted 0.5 each side):

    L = - sum over pairs:
            log sigmoid(s(winner) - s(loser))      if win/loss
          + 0.5 * (log sigmoid(s(A)-s(B)) + log sigmoid(s(B)-s(A)))  if tie

Optimizer: pure-numpy gradient descent with Adam (no scipy / torch
dep). For ~50 observations and 6 features the fit converges in <1s
on CPU.

Usage:
    python scripts/train/train_quality_model.py
    python scripts/train/train_quality_model.py --in pairs.jsonl --out model.json
    python scripts/train/train_quality_model.py --epochs 2000 --lr 0.05

Default input:  $EDA_AGENT_PREFERENCES_LOG OR ~/.eda-agent/pairwise_preferences.jsonl
Default output: <repo>/src/eda_agent/design/quality_model.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# The features the trainer fits weights for. Order is preserved in the
# output JSON so inference can replay the same ordering.
_FEATURE_NAMES = (
    "wire_crossings",
    "wires_through_bodies",
    "body_overlaps",
    "aspect_ratio_penalty",
    "total_wire_length",
    "port_count",
)


REPO = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO / "src" / "eda_agent" / "design" / "quality_model.json"


def _default_input_path() -> Path:
    override = os.environ.get("EDA_AGENT_PREFERENCES_LOG")
    if override:
        return Path(override)
    userprofile = os.environ.get("USERPROFILE")
    base = Path(userprofile) if userprofile else Path.home()
    return base / ".eda-agent" / "pairwise_preferences.jsonl"


def _load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _feature_vector(features: dict[str, float]) -> list[float]:
    return [float(features.get(name, 0.0)) for name in _FEATURE_NAMES]


def _sigmoid(z: float) -> float:
    # Numerically stable.
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


def _log_sigmoid(z: float) -> float:
    # log sigmoid(z) = -log(1 + exp(-z))
    if z >= 0:
        return -math.log1p(math.exp(-z))
    return z - math.log1p(math.exp(z))


def _normalise_features(
    rows: list[dict[str, Any]],
) -> tuple[list[tuple[list[float], list[float], str]], list[float], list[float]]:
    """Z-score the raw counts across the whole dataset.

    Wire crossings and total_wire_length live on very different scales
    (units vs thousands). Standardising puts them on comparable footing
    so gradient descent converges and the learned weights are directly
    interpretable as 'importance per standard deviation'.

    Returns: (preprocessed_rows, mean, std).
    """
    all_features: list[list[float]] = []
    for row in rows:
        all_features.append(_feature_vector(row["features_a"]))
        all_features.append(_feature_vector(row["features_b"]))
    if not all_features:
        return [], [0.0] * len(_FEATURE_NAMES), [1.0] * len(_FEATURE_NAMES)
    n = len(all_features)
    means = [
        sum(f[i] for f in all_features) / n for i in range(len(_FEATURE_NAMES))
    ]
    stds = []
    for i in range(len(_FEATURE_NAMES)):
        var = sum((f[i] - means[i]) ** 2 for f in all_features) / n
        stds.append(max(math.sqrt(var), 1e-6))
    out = []
    for row in rows:
        a = _feature_vector(row["features_a"])
        b = _feature_vector(row["features_b"])
        a_n = [(a[i] - means[i]) / stds[i] for i in range(len(_FEATURE_NAMES))]
        b_n = [(b[i] - means[i]) / stds[i] for i in range(len(_FEATURE_NAMES))]
        out.append((a_n, b_n, row["winner"]))
    return out, means, stds


def _train(
    pairs: list[tuple[list[float], list[float], str]],
    *,
    epochs: int = 2000,
    lr: float = 0.05,
    l2: float = 0.01,
) -> tuple[list[float], list[float]]:
    """Pure-Python Adam-style logistic regression on Bradley-Terry loss.

    Returns (final_weights, loss_history).
    """
    F = len(_FEATURE_NAMES)
    w = [0.0] * F
    # Adam moments.
    m = [0.0] * F
    v = [0.0] * F
    beta1, beta2, eps = 0.9, 0.999, 1e-8
    history: list[float] = []
    for epoch in range(1, epochs + 1):
        grad = [0.0] * F
        total_loss = 0.0
        for (a, b, winner) in pairs:
            # diff = a - b (per-feature)
            diff = [a[i] - b[i] for i in range(F)]
            # score margin = w . diff
            margin = sum(w[i] * diff[i] for i in range(F))
            if winner == "a":
                # P(a beats b) = sigmoid(margin); maximize log P -> minimize -log sigmoid(margin)
                p = _sigmoid(margin)
                total_loss -= _log_sigmoid(margin)
                # grad of -log sigmoid(margin) w.r.t. w_i = -(1-p) * diff[i]
                for i in range(F):
                    grad[i] -= (1.0 - p) * diff[i]
            elif winner == "b":
                # P(b beats a) = sigmoid(-margin)
                p = _sigmoid(-margin)
                total_loss -= _log_sigmoid(-margin)
                for i in range(F):
                    grad[i] -= -(1.0 - p) * diff[i]
            elif winner == "tie":
                # Treat as 0.5 weight on each direction.
                pa = _sigmoid(margin)
                pb = _sigmoid(-margin)
                total_loss -= 0.5 * (_log_sigmoid(margin) + _log_sigmoid(-margin))
                for i in range(F):
                    grad[i] -= 0.5 * (1.0 - pa) * diff[i]
                    grad[i] -= 0.5 * -(1.0 - pb) * diff[i]
        # L2 regularisation.
        for i in range(F):
            grad[i] += l2 * w[i]
            total_loss += 0.5 * l2 * w[i] * w[i]
        # Adam update.
        for i in range(F):
            m[i] = beta1 * m[i] + (1.0 - beta1) * grad[i]
            v[i] = beta2 * v[i] + (1.0 - beta2) * grad[i] * grad[i]
            m_hat = m[i] / (1.0 - beta1 ** epoch)
            v_hat = v[i] / (1.0 - beta2 ** epoch)
            w[i] -= lr * m_hat / (math.sqrt(v_hat) + eps)
        history.append(total_loss)
    return w, history


def _eval_accuracy(
    pairs: list[tuple[list[float], list[float], str]],
    w: list[float],
) -> dict[str, Any]:
    """How well do the learned weights predict the observed preferences?

    Pairs where winner='tie' are counted as 'correct' iff |margin| <
    margin_tol; otherwise 'tie-loss'.
    """
    F = len(_FEATURE_NAMES)
    correct = 0
    total = 0
    tie_loss = 0
    for (a, b, winner) in pairs:
        margin = sum(w[i] * (a[i] - b[i]) for i in range(F))
        if winner == "a":
            total += 1
            if margin > 0:
                correct += 1
        elif winner == "b":
            total += 1
            if margin < 0:
                correct += 1
        elif winner == "tie":
            total += 1
            if abs(margin) < 0.1:
                correct += 1
            else:
                tie_loss += 1
    return {
        "n_pairs": total,
        "n_correct": correct,
        "accuracy": correct / total if total else 0.0,
        "tie_loss": tie_loss,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--in", dest="input_path", type=Path,
                        default=_default_input_path())
    parser.add_argument("--out", dest="output_path", type=Path,
                        default=DEFAULT_OUT)
    parser.add_argument("--epochs", type=int, default=2000)
    parser.add_argument("--lr", type=float, default=0.05)
    parser.add_argument("--l2", type=float, default=0.01)
    parser.add_argument("--min-pairs", type=int, default=5,
                        help="Refuse to fit until at least this many pairs are logged.")
    args = parser.parse_args(argv)

    rows = _load_rows(args.input_path)
    if len(rows) < args.min_pairs:
        print(
            f"Only {len(rows)} pairs in {args.input_path}; need at least "
            f"{args.min_pairs}. Vote more before training (or pass --min-pairs).",
            file=sys.stderr,
        )
        return 1

    pairs, means, stds = _normalise_features(rows)
    w, history = _train(
        pairs, epochs=args.epochs, lr=args.lr, l2=args.l2,
    )
    metrics = _eval_accuracy(pairs, w)

    # The "raw" weight (per-feature, untransformed) is what the
    # pipeline applies at inference: divide each normalised weight
    # by std so the math at inference is just w_raw . features_raw.
    raw_w = [w[i] / stds[i] for i in range(len(_FEATURE_NAMES))]
    raw_intercept = -sum(raw_w[i] * means[i] for i in range(len(_FEATURE_NAMES)))

    payload = {
        "version": 1,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "n_pairs": len(rows),
        "features": list(_FEATURE_NAMES),
        # Normalised (z-score) weights -- useful for inspecting which
        # feature MATTERS the most per standard deviation.
        "weights_normalised": {
            name: float(w[i]) for i, name in enumerate(_FEATURE_NAMES)
        },
        # Raw weights -- applied directly to feature counts at inference.
        # Final score = sum(raw_w_i * feature_i) + raw_intercept.
        "weights_raw": {
            name: float(raw_w[i]) for i, name in enumerate(_FEATURE_NAMES)
        },
        "intercept_raw": float(raw_intercept),
        "feature_means": {
            name: float(means[i]) for i, name in enumerate(_FEATURE_NAMES)
        },
        "feature_stds": {
            name: float(stds[i]) for i, name in enumerate(_FEATURE_NAMES)
        },
        "training_metrics": metrics,
        "final_loss": history[-1] if history else None,
    }
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    args.output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(
        f"\nTrained on {len(rows)} pairs in {len(history)} epochs.\n"
        f"  final_loss = {payload['final_loss']:.4f}\n"
        f"  accuracy   = {metrics['accuracy']:.1%}  "
        f"({metrics['n_correct']}/{metrics['n_pairs']})\n"
        f"  weights (per stddev):"
    )
    for name in _FEATURE_NAMES:
        print(f"    {name:24s} {payload['weights_normalised'][name]:+.3f}")
    print(f"\n  Wrote {args.output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

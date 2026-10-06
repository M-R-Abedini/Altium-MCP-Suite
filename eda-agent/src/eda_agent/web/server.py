# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Local HTTP UI for pairwise layout-preference voting.

Run with:
    eda-agent vote --plan PLAN.json --symbols SYMBOLS.json

Opens http://localhost:8765 in your browser. You see two SVG layouts of
the same plan side-by-side. Click "A is better", "B is better", or "Tie".
The server records the vote, then immediately generates the next pair.
Repeat for as long as you want to build training data.

Why HTTP + browser (not CLI / MCP): side-by-side image comparison is
where pairwise preferences pay off, and that needs a real display.
SVGs in a browser scale to any window size; the buttons are obvious;
you can vote 30 pairs in 5 minutes once you've calibrated your taste.
The recorded JSONL is the same shape the offline training script
consumes, regardless of how the vote was captured.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional

from eda_agent.design.plan import DesignPlan
from eda_agent.design.preferences import (
    present_pair,
    present_tournament,
    record_preference,
    record_tournament,
    _count_records,
    _pref_log_path,
)
from eda_agent.design.symbols import SymbolExtractor

logger = logging.getLogger("eda_agent.web.server")


def _build_static_extractor(symbols_path: Path) -> SymbolExtractor:
    """Wrap the offline symbol-fixture loader from scripts/dev."""
    # Inline the fixture loader so we don't depend on the dev script's
    # file location (it's a sibling but importing from scripts/ is
    # awkward across installs).
    from eda_agent.design.symbols import (
        SymbolBBox,
        SymbolModel,
        SymbolPin,
        _bbox_from_pins,
    )

    class _StaticExtractor(SymbolExtractor):
        def __init__(self, syms):
            self._symbols = syms

        def extract_one(self, lib_path, lib_ref):
            return self._symbols.get((lib_path, lib_ref))

        def extract_many(self, refs):
            return {
                (lp, lr): self._symbols[(lp, lr)]
                for (lp, lr) in refs
                if (lp, lr) in self._symbols
            }

    data = json.loads(symbols_path.read_text(encoding="utf-8"))
    out = {}
    for entry in data:
        pins = tuple(
            SymbolPin(
                designator=str(p["designator"]),
                name=str(p.get("name", p["designator"])),
                x=int(p["x"]), y=int(p["y"]),
                orientation=int(p.get("orientation", 0)) % 4,
                length=int(p.get("length", 200)),
                electrical_type=str(p.get("electrical_type", "passive")),
                hidden=bool(p.get("hidden", False)),
            )
            for p in entry["pins"]
        )
        bbox_raw = entry.get("body_bbox")
        if bbox_raw:
            bbox = SymbolBBox(
                x_min=int(bbox_raw["x_min"]), y_min=int(bbox_raw["y_min"]),
                x_max=int(bbox_raw["x_max"]), y_max=int(bbox_raw["y_max"]),
            )
        else:
            bbox = _bbox_from_pins(pins)
        model = SymbolModel(
            lib_path=entry["lib_path"], lib_ref=entry["lib_ref"],
            pins=pins, body_bbox=bbox,
            designator_prefix=entry.get("designator_prefix", "U"),
            description=entry.get("description", ""),
        )
        out[(model.lib_path, model.lib_ref)] = model
    return _StaticExtractor(out)


def _build_live_extractor() -> SymbolExtractor:
    """Use the real Altium bridge + on-disk symbol cache."""
    from eda_agent.bridge.altium_bridge import get_bridge
    from eda_agent.design.symbols import SymbolCache

    bridge = get_bridge()
    cache_dir = Path(__file__).resolve().parents[3] / ".symbol_cache"
    return SymbolExtractor(bridge, SymbolCache(cache_dir))


_EDITOR_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>eda-agent: drag-edit layout</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         margin: 0; padding: 16px; background: #f4f4f4; color: #222; }
  h1 { margin: 0 0 6px 0; font-size: 18px; }
  .stats { color: #666; font-size: 13px; margin-bottom: 12px; }
  .toolbar { display: flex; gap: 10px; align-items: center; margin-bottom: 12px; }
  .toolbar button, .toolbar a {
    padding: 9px 18px; border: 0; border-radius: 6px; cursor: pointer;
    color: white; font-weight: 600; font-size: 14px; text-decoration: none;
    display: inline-block;
  }
  button.save { background: #2c7a2c; }
  button.reset { background: #888; }
  a.tournament { background: #0066cc; }
  .canvas-wrap { background: white; border: 1px solid #ddd; border-radius: 6px;
                 padding: 10px; box-shadow: 0 1px 4px rgba(0,0,0,0.05); }
  .canvas-wrap svg { display: block; width: 100%; height: auto; }
  .component { cursor: move; }
  .component:hover rect { stroke-width: 2; stroke: #2c7a2c; }
  .component.dragging { opacity: 0.7; cursor: grabbing; }
  .component.hovered rect { stroke: #2c7a2c; stroke-width: 2; }
  .power-port { cursor: move; }
  .power-port:hover line, .power-port:hover circle, .power-port:hover polygon, .power-port:hover path {
    stroke: #2c7a2c !important; stroke-width: 3 !important;
  }
  .power-port.dragging { opacity: 0.6; }
  .score { color: #666; font-size: 13px; margin-top: 8px; font-family: monospace; }
  .err { color: #b03030; padding: 12px; background: #fee; border-radius: 6px; }
  kbd { background: #eee; padding: 2px 6px; border-radius: 3px; font-family: monospace;
        font-size: 12px; border-bottom: 2px solid #ccc; }
  .toast {
    position: fixed; bottom: 20px; right: 20px;
    background: #2c7a2c; color: white;
    padding: 12px 18px; border-radius: 6px; font-weight: 600;
    box-shadow: 0 2px 8px rgba(0,0,0,0.2);
    opacity: 0; transition: opacity 0.3s;
  }
  .toast.show { opacity: 1; }
</style>
</head>
<body>
<h1>Drag parts to reposition them</h1>
<div class="stats">
  Plan: $plan_name &middot; Saved layouts: $n_saved &middot; Model: $model_status
  <br>
  Keys: drag to move &middot; hover + <kbd>R</kbd> rotate 90&deg; &middot;
  hover + <kbd>F</kbd> flip &middot; <kbd>S</kbd> save &middot;
  <kbd>shift</kbd>+<kbd>R</kbd> reset
</div>
<div class="toolbar">
  <form method="post" action="/save_layout" style="display:inline;">
    <button type="submit" class="save">Save layout (S)</button>
  </form>
  <form method="post" action="/reset_edits" style="display:inline;">
    <button type="submit" class="reset">Reset to algorithmic placement (R)</button>
  </form>
  <a class="tournament" href="/tournament">Switch to tournament mode</a>
</div>
<div class="canvas-wrap">
  $svg
</div>
<div class="score">$score_line</div>
<div class="toast" id="toast"></div>
<script>
  // Drag-and-drop + rotate/flip/port-drag.
  // Components AND power ports are draggable; rotate (R) and flip (F)
  // only apply to components. The mouse-to-mil conversion uses the
  // scale + sheet dims encoded on the <svg> root.
  (function() {
    var svg = document.querySelector('.canvas-wrap svg');
    if (!svg) return;
    var scale = parseFloat(svg.dataset.scale || '0.1');
    var dragging = null;
    var draggingType = null; // 'component' | 'power-port'
    var dragStart = null;
    var dragOrigin = null;
    var hovered = null; // last component or port the mouse hovered over

    function svgPoint(evt) {
      var pt = svg.createSVGPoint();
      pt.x = evt.clientX; pt.y = evt.clientY;
      return pt.matrixTransform(svg.getScreenCTM().inverse());
    }

    function attachDrag(g, type) {
      g.addEventListener('mouseenter', function() {
        hovered = { el: g, type: type };
        g.classList.add('hovered');
      });
      g.addEventListener('mouseleave', function() {
        g.classList.remove('hovered');
        if (hovered && hovered.el === g) hovered = null;
      });
      g.addEventListener('mousedown', function(e) {
        e.preventDefault();
        dragging = g;
        draggingType = type;
        g.classList.add('dragging');
        dragStart = svgPoint(e);
        var match = (g.getAttribute('transform') || 'translate(0,0)').match(
          /translate\\(([-\\d.]+)[ ,]([-\\d.]+)\\)/);
        dragOrigin = {
          x: match ? parseFloat(match[1]) : 0,
          y: match ? parseFloat(match[2]) : 0,
        };
      });
    }

    document.querySelectorAll('.component').forEach(function(g) {
      attachDrag(g, 'component');
    });
    document.querySelectorAll('.power-port').forEach(function(g) {
      attachDrag(g, 'power-port');
    });

    document.addEventListener('mousemove', function(e) {
      if (!dragging) return;
      var p = svgPoint(e);
      var dx = p.x - dragStart.x;
      var dy = p.y - dragStart.y;
      dragging.setAttribute('transform',
        'translate(' + (dragOrigin.x + dx).toFixed(1) + ',' +
        (dragOrigin.y + dy).toFixed(1) + ')');
    });

    document.addEventListener('mouseup', function(e) {
      if (!dragging) return;
      var origXMils = parseInt(dragging.dataset.xMils, 10);
      var origYMils = parseInt(dragging.dataset.yMils, 10);
      var p = svgPoint(e);
      var dx_svg = p.x - dragStart.x;
      var dy_svg = p.y - dragStart.y;
      var dx_mils = Math.round(dx_svg / scale / 100) * 100;
      var dy_mils = -Math.round(dy_svg / scale / 100) * 100;
      var newX = origXMils + dx_mils;
      var newY = origYMils + dy_mils;
      var type = draggingType;
      var el = dragging;
      dragging.classList.remove('dragging');
      dragging = null;
      draggingType = null;

      // Skip server roundtrip on tiny moves.
      if (Math.abs(dx_mils) < 50 && Math.abs(dy_mils) < 50) return;

      var form = new FormData();
      form.append('x_mils', newX);
      form.append('y_mils', newY);
      var endpoint;
      if (type === 'component') {
        form.append('refdes', el.dataset.refdes);
        form.append('rotation', parseInt(el.dataset.rotation, 10));
        form.append('flipped', el.dataset.flipped === '1' ? '1' : '0');
        endpoint = '/update_position';
      } else {
        form.append('net', el.dataset.net);
        endpoint = '/update_port';
      }
      fetch(endpoint, { method: 'POST', body: form })
        .then(function(r) { return r.json(); })
        .then(function(_) { window.location.reload(); });
    });

    document.addEventListener('keydown', function(e) {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
      var k = e.key.toLowerCase();
      if (k === 's' && !e.shiftKey) {
        e.preventDefault();
        document.querySelector('button.save').closest('form').submit();
      } else if (k === 'r' && e.shiftKey) {
        e.preventDefault();
        document.querySelector('button.reset').closest('form').submit();
      } else if ((k === 'r' || k === 'f') && hovered && hovered.type === 'component') {
        e.preventDefault();
        var el = hovered.el;
        var form = new FormData();
        form.append('refdes', el.dataset.refdes);
        form.append('x_mils', parseInt(el.dataset.xMils, 10));
        form.append('y_mils', parseInt(el.dataset.yMils, 10));
        if (k === 'r') {
          var rot = (parseInt(el.dataset.rotation, 10) + 90) % 360;
          form.append('rotation', rot);
          form.append('flipped', el.dataset.flipped === '1' ? '1' : '0');
        } else {
          form.append('rotation', parseInt(el.dataset.rotation, 10));
          form.append('flipped', el.dataset.flipped === '1' ? '0' : '1');
        }
        fetch('/update_position', { method: 'POST', body: form })
          .then(function(r) { return r.json(); })
          .then(function(_) { window.location.reload(); });
      }
    });

    // Toast feedback after save / reset (URL fragment).
    if (window.location.hash === '#saved') {
      var t = document.getElementById('toast');
      t.textContent = 'Layout saved: used as training data.';
      t.classList.add('show');
      setTimeout(function() { t.classList.remove('show'); }, 2000);
      history.replaceState(null, '', window.location.pathname);
    }
  })();
</script>
</body>
</html>"""


_TOURNAMENT_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>eda-agent: pick the best layout</title>
<style>
  :root {
    --bg: #f4f4f4; --card: #fff; --good: #2c7a2c; --accent: #0066cc;
    --champ: #f6d35a; --txt: #222;
  }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         margin: 0; padding: 20px; background: var(--bg); color: var(--txt); }
  h1 { margin: 0 0 6px 0; font-size: 18px; }
  .stats { color: #666; font-size: 13px; margin-bottom: 16px; }
  .grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
  .card { background: var(--card); border: 1px solid #ddd; border-radius: 6px;
          padding: 10px; cursor: pointer; transition: transform 0.06s, box-shadow 0.06s;
          position: relative; }
  .card:hover { transform: translateY(-1px); box-shadow: 0 3px 8px rgba(0,0,0,0.1);
                border-color: var(--good); }
  .card.champion { border-color: var(--champ); border-width: 2px; }
  .badge { position: absolute; top: 6px; right: 6px; background: var(--champ);
           color: #5a4400; padding: 2px 8px; border-radius: 4px; font-size: 11px;
           font-weight: 600; }
  .card h2 { margin: 0 0 4px 0; font-size: 13px; color: #666; }
  .card .score { font-size: 11px; color: #888; margin-bottom: 6px; font-family: monospace; }
  .svgwrap { background: #fff; border: 1px solid #eee; }
  .svgwrap svg { display: block; width: 100%; height: auto; max-height: 35vh; }
  .actions { margin-top: 16px; display: flex; gap: 10px; justify-content: center; }
  button.skip { padding: 10px 20px; background: #888; color: white; border: 0;
                border-radius: 6px; cursor: pointer; font-size: 14px; }
  button.skip:hover { background: #b03030; }
  .footer { margin-top: 20px; color: #888; font-size: 12px; text-align: center; }
  kbd { background: #eee; padding: 2px 6px; border-radius: 3px; font-family: monospace;
        font-size: 12px; border-bottom: 2px solid #ccc; }
</style>
</head>
<body>
<h1>Click the layout you like best</h1>
<div class="stats">
  Plan: $plan_name &middot; Pairwise records logged: $n_votes &middot; Model: $model_status
  &middot; Keyboard: <kbd>1</kbd>-<kbd>6</kbd> to pick &middot; <kbd>X</kbd> reroll
</div>
<div class="grid">
$cards
</div>
<div class="actions">
  <form method="post" action="/skip_round"><input type="hidden" name="round_id" value="$round_id"><button class="skip">Reroll &mdash; none are good (X)</button></form>
</div>
<div class="footer">Log: $log_path &middot; Round: $round_id</div>
<script>
  // Number keys 1-6 click the corresponding card; X rerolls.
  document.addEventListener('keydown', function(e) {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    var key = e.key.toLowerCase();
    if (key >= '1' && key <= '6') {
      var idx = parseInt(key, 10) - 1;
      var cards = document.querySelectorAll('.card');
      if (cards[idx]) cards[idx].click();
    } else if (key === 'x') {
      document.querySelector('button.skip').closest('form').submit();
    }
  });
  // Each card is a clickable form-submission button.
  document.querySelectorAll('.card').forEach(function(card) {
    card.addEventListener('click', function() {
      var form = document.createElement('form');
      form.method = 'POST';
      form.action = '/pick';
      form.style.display = 'none';
      var roundInput = document.createElement('input');
      roundInput.name = 'round_id';
      roundInput.value = card.dataset.roundId;
      var candInput = document.createElement('input');
      candInput.name = 'candidate_id';
      candInput.value = card.dataset.candidateId;
      form.appendChild(roundInput);
      form.appendChild(candInput);
      document.body.appendChild(form);
      form.submit();
    });
  });
</script>
</body>
</html>"""


_PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>eda-agent: pairwise vote</title>
<style>
  :root {
    --bg: #f4f4f4;
    --card: #fff;
    --accent: #0066cc;
    --good: #2c7a2c;
    --tie: #777;
    --bad: #b03030;
    --txt: #222;
  }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         margin: 0; padding: 20px; background: var(--bg); color: var(--txt); }
  h1 { margin: 0 0 10px 0; font-size: 18px; }
  .stats { color: #666; font-size: 13px; margin-bottom: 20px; }
  .pair { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
  .card { background: var(--card); border: 1px solid #ddd; border-radius: 6px;
          padding: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }
  .card h2 { margin: 0 0 6px 0; font-size: 14px; color: #666; }
  .card .score { font-size: 12px; color: #888; margin-bottom: 6px; font-family: monospace; }
  .svgwrap { background: #fff; border: 1px solid #eee; }
  .svgwrap svg { display: block; width: 100%; height: auto; max-height: 70vh; }
  .actions { margin-top: 20px; display: flex; gap: 10px; justify-content: center; }
  .actions form { display: inline; }
  button { padding: 12px 28px; font-size: 15px; border: 0; border-radius: 6px;
           cursor: pointer; color: white; font-weight: 600; }
  .vote-a { background: var(--good); }
  .vote-b { background: var(--good); }
  .vote-tie { background: var(--tie); }
  .vote-bad { background: var(--bad); }
  button:hover { opacity: 0.9; }
  .err { color: var(--bad); padding: 12px; background: #fee; border-radius: 6px; }
  .footer { margin-top: 30px; color: #888; font-size: 12px; text-align: center; }
  kbd { background: #eee; padding: 2px 6px; border-radius: 3px; font-family: monospace;
        font-size: 12px; border-bottom: 2px solid #ccc; }
</style>
</head>
<body>
<h1>Which layout is better?</h1>
<div class="stats">
  Plan: $plan_name &middot; Votes so far: $n_votes &middot; Model status: $model_status
  &middot; Keyboard: <kbd>A</kbd> / <kbd>B</kbd> / <kbd>T</kbd> (tie) / <kbd>X</kbd> (both bad)
</div>
<div class="pair">
  <div class="card">
    <h2>Variant A</h2>
    <div class="score">score=$score_a &middot; crossings=$cross_a &middot; through_body=$tb_a &middot; overlaps=$ov_a &middot; length=$len_a</div>
    <div class="svgwrap">$svg_a</div>
  </div>
  <div class="card">
    <h2>Variant B</h2>
    <div class="score">score=$score_b &middot; crossings=$cross_b &middot; through_body=$tb_b &middot; overlaps=$ov_b &middot; length=$len_b</div>
    <div class="svgwrap">$svg_b</div>
  </div>
</div>
<div class="actions">
  <form method="post" action="/vote"><input type="hidden" name="pair_id" value="$pair_id"><input type="hidden" name="winner" value="a"><button class="vote-a">A is better</button></form>
  <form method="post" action="/vote"><input type="hidden" name="pair_id" value="$pair_id"><input type="hidden" name="winner" value="b"><button class="vote-b">B is better</button></form>
  <form method="post" action="/vote"><input type="hidden" name="pair_id" value="$pair_id"><input type="hidden" name="winner" value="tie"><button class="vote-tie">Tie</button></form>
  <form method="post" action="/vote"><input type="hidden" name="pair_id" value="$pair_id"><input type="hidden" name="winner" value="bad"><button class="vote-bad">Both bad &mdash; skip</button></form>
</div>
<div class="footer">Log: $log_path</div>
<script>
  document.addEventListener('keydown', function(e) {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    var key = e.key.toLowerCase();
    var sel = null;
    if (key === 'a') sel = 'a';
    else if (key === 'b') sel = 'b';
    else if (key === 't') sel = 'tie';
    else if (key === 'x') sel = 'bad';
    if (sel) {
      var form = document.querySelector('form input[value="' + sel + '"]').closest('form');
      form.submit();
    }
  });
</script>
</body>
</html>"""


_ERROR_HTML = """<!doctype html>
<html><body style="font-family:sans-serif;padding:40px;">
<h1>eda-agent vote</h1>
<div style="background:#fee;color:#b03030;padding:16px;border-radius:6px;">
  <strong>Could not build a pair:</strong><br>
  <pre>{error}</pre>
</div>
<p><a href="/">Try again</a></p>
</body></html>"""


def create_app(plan: DesignPlan, extractor: SymbolExtractor, plan_name: str = "design"):
    """Build the Flask app. Plan + extractor are bound once at startup.

    Each GET / generates a fresh pair (different layouts) for the same
    plan. Each POST /vote records the choice and redirects to / for
    the next pair.
    """
    try:
        from flask import Flask, redirect, request
    except ImportError as exc:
        raise ImportError(
            "Flask is required for the vote UI. "
            "Install with `pip install eda-agent[web]`."
        ) from exc

    app = Flask(__name__)

    # In-process state:
    # - champion_canvas: tournament-mode persistent winner
    # - edit_hints: drag-editor's accumulated placement_hints
    # - saved_layouts_count: how many "Save layout" clicks so far
    state: dict[str, Any] = {
        "champion_canvas": None,
        "edit_hints": {},
        "port_hints": {},
        "saved_layouts_count": 0,
    }

    def _render_editor():
        from string import Template
        from eda_agent.design.pipeline import build_best_canvas_from_plan
        from eda_agent.design.quality import _load_quality_model, score_canvas
        from eda_agent.design.render_svg import render_canvas_svg
        result = build_best_canvas_from_plan(
            plan, extractor,
            placement_hints=state["edit_hints"],
            port_hints=state["port_hints"],
            strict_shorts=False,
        )
        if not result.ok or not result.canvas.instances:
            return _ERROR_HTML.format(
                error="pipeline failed: " + "; ".join(
                    f.text for f in result.failures
                ),
            )
        score = score_canvas(result.canvas, plan)
        svg = render_canvas_svg(result.canvas)
        if svg.startswith("<?xml"):
            svg = svg.split("?>", 1)[-1]
        model = _load_quality_model()
        if model:
            acc = model.get("training_metrics", {}).get("accuracy", 0)
            model_status = (
                f"learned from {model.get('n_pairs', '?')} votes "
                f"(acc {acc * 100:.0f}%)"
            )
        else:
            model_status = "heuristic (no votes / layouts trained yet)"
        score_line = (
            f"score={score.total:.1f} &middot; crossings={score.wire_crossings} "
            f"&middot; through-body={score.wires_through_bodies} "
            f"&middot; overlaps={score.body_overlaps} "
            f"&middot; aspect={score.aspect_ratio_penalty:.2f} "
            f"&middot; wire-length={score.total_wire_length}"
        )
        return Template(_EDITOR_HTML).safe_substitute(
            plan_name=plan_name,
            n_saved=state["saved_layouts_count"],
            model_status=model_status,
            svg=svg,
            score_line=score_line,
        )

    @app.route("/")
    def index():
        return _render_editor()

    @app.route("/update_position", methods=["POST"])
    def update_position():
        refdes = request.form.get("refdes", "").strip()
        try:
            x_mils = int(request.form.get("x_mils", "0"))
            y_mils = int(request.form.get("y_mils", "0"))
            rotation = int(request.form.get("rotation", "0"))
            flipped = request.form.get("flipped", "0") == "1"
        except ValueError:
            return {"ok": False, "error": "bad coords"}, 400
        if not refdes:
            return {"ok": False, "error": "missing refdes"}, 400
        state["edit_hints"][refdes] = {
            "x": x_mils, "y": y_mils, "rotation": rotation,
            "flipped": flipped,
        }
        return {"ok": True, "hints": state["edit_hints"]}

    @app.route("/update_port", methods=["POST"])
    def update_port():
        net = request.form.get("net", "").strip()
        try:
            x_mils = int(request.form.get("x_mils", "0"))
            y_mils = int(request.form.get("y_mils", "0"))
        except ValueError:
            return {"ok": False, "error": "bad coords"}, 400
        if not net:
            return {"ok": False, "error": "missing net"}, 400
        state["port_hints"][net] = {"x": x_mils, "y": y_mils}
        return {"ok": True, "port_hints": state["port_hints"]}

    @app.route("/save_layout", methods=["POST"])
    def save_layout():
        # Snapshot the current canvas (with accumulated edit_hints
        # applied) as a saved layout. We do two things:
        #   1. Log each user-edited refdes to placement_edits.jsonl,
        #      the shape the priors aggregator consumes.
        #   2. Generate synthetic pairwise rows (user_layout > random
        #      alternative) so the BT model gets immediate signal.
        from eda_agent.design.pipeline import build_best_canvas_from_plan
        from eda_agent.design.preferences import (
            _generate_contrast_variant, _features_from_score,
            PairwiseRecord, _pref_log_path, _plan_hash, _retrain_inline,
        )
        from eda_agent.design.quality import score_canvas
        import json as _json
        from dataclasses import asdict
        import time as _time
        if not state["edit_hints"]:
            return redirect("/", code=303)
        result = build_best_canvas_from_plan(
            plan, extractor,
            placement_hints=state["edit_hints"],
            strict_shorts=False,
        )
        if not result.ok:
            return redirect("/", code=303)
        # 1. placement_edits.jsonl: per-refdes deltas.
        baseline = build_best_canvas_from_plan(
            plan, extractor, strict_shorts=False,
        )
        baseline_pos = {
            i.refdes: (i.x, i.y, i.rotation)
            for i in baseline.canvas.instances
        }
        edit_log = Path.home() / ".eda-agent" / "placement_edits.jsonl"
        edit_log.parent.mkdir(parents=True, exist_ok=True)
        plan_h = _plan_hash(plan)
        # 1b. Canvas snapshot, persisted alongside the JSONL keyed by
        # design_id so post-edit positions are retrievable for any
        # downstream analysis that needs absolute coords (vs the
        # priors aggregator + BT scorer which use deltas only).
        snapshot_dir = Path.home() / ".eda-agent" / "design_snapshots"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot_payload = {
            "plan": plan.model_dump(),
            "canvas": result.canvas.to_dict(),
            "ts": _time.time(),
        }
        (snapshot_dir / f"{plan_h}.canvas.json").write_text(
            _json.dumps(snapshot_payload, indent=2), encoding="utf-8",
        )
        n_rows = 0
        with edit_log.open("a", encoding="utf-8") as f:
            for inst in result.canvas.instances:
                base = baseline_pos.get(inst.refdes)
                if base is None:
                    continue
                bx, by, brot = base
                if (bx, by, brot) == (inst.x, inst.y, inst.rotation):
                    continue
                f.write(_json.dumps({
                    "ts": _time.time(),
                    "design_id": plan_h,
                    "refdes": inst.refdes,
                    "part_role": "", "part_lib_ref": inst.symbol.lib_ref,
                    "anchor_refdes": "", "anchor_role": "",
                    "anchor_lib_ref": "",
                    "dx_mils": inst.x - bx,
                    "dy_mils": inst.y - by,
                    "rot_delta_deg": ((inst.rotation - brot) % 360),
                    "design_size": len(result.canvas.instances),
                    "source": "drag-edit",
                }) + "\n")
                n_rows += 1
        # 2. Synthetic pairwise rows: user's saved layout beats N random alternatives.
        user_score = score_canvas(result.canvas, plan)
        user_features = _features_from_score(user_score)
        contrast = _generate_contrast_variant(plan, extractor, result)
        prefs_log = _pref_log_path()
        prefs_log.parent.mkdir(parents=True, exist_ok=True)
        with prefs_log.open("a", encoding="utf-8") as f:
            if contrast.ok and contrast.canvas.instances:
                contrast_score = score_canvas(contrast.canvas, plan)
                f.write(_json.dumps(asdict(PairwiseRecord(
                    pair_id=f"save_{_time.time():.0f}",
                    plan_hash=plan_h,
                    winner="a",
                    features_a=user_features,
                    features_b=_features_from_score(contrast_score),
                    user_note="drag-edit save",
                ))) + "\n")
        # Hot-retrain the model so the next page load reflects the new data.
        _retrain_inline(prefs_log)
        state["saved_layouts_count"] += 1
        return redirect("/#saved", code=303)

    @app.route("/reset_edits", methods=["POST"])
    def reset_edits():
        state["edit_hints"] = {}
        state["port_hints"] = {}
        return redirect("/", code=303)

    @app.route("/tournament")
    def tournament_index():
        from string import Template
        from eda_agent.design.quality import _load_quality_model
        tour = present_tournament(
            plan, extractor,
            champion_canvas=state["champion_canvas"], n=6,
        )
        if not tour.get("ok"):
            return _ERROR_HTML.format(error=tour.get("error", "unknown"))
        model = _load_quality_model()
        if model:
            acc = model.get("training_metrics", {}).get("accuracy", 0)
            model_status = (
                f"learned from {model.get('n_pairs', '?')} votes "
                f"(acc {acc * 100:.0f}%)"
            )
        else:
            model_status = "heuristic (no votes trained yet)"
        cards_html = []
        for cand in tour["candidates"]:
            svg = Path(cand["svg_path"]).read_text(encoding="utf-8")
            if svg.startswith("<?xml"):
                svg = svg.split("?>", 1)[-1]
            badge = '<div class="badge">CHAMPION</div>' if cand["is_champion"] else ""
            klass = "card champion" if cand["is_champion"] else "card"
            cards_html.append(f"""
              <div class="{klass}" data-round-id="{tour['round_id']}" data-candidate-id="{cand['candidate_id']}">
                {badge}
                <h2>{cand['profile_name']}</h2>
                <div class="score">score={cand['score']:.1f} &middot; crossings={int(cand['features']['wire_crossings'])} &middot; overlaps={int(cand['features']['body_overlaps'])} &middot; len={int(cand['features']['total_wire_length'])}</div>
                <div class="svgwrap">{svg}</div>
              </div>""")
        return Template(_TOURNAMENT_HTML).safe_substitute(
            plan_name=plan_name,
            n_votes=tour["n_records"],
            model_status=model_status,
            round_id=tour["round_id"],
            cards="\n".join(cards_html),
            log_path=str(_pref_log_path()),
        )

    @app.route("/pick", methods=["POST"])
    def pick():
        round_id = request.form.get("round_id", "").strip()
        candidate_id = request.form.get("candidate_id", "").strip()
        if not round_id or not candidate_id:
            return redirect("/", code=303)
        result = record_tournament(round_id, candidate_id)
        if result.get("ok"):
            state["champion_canvas"] = result["winner_canvas"]
        else:
            logger.warning("record_tournament failed: %s", result)
        return redirect("/", code=303)

    @app.route("/skip_round", methods=["POST"])
    def skip_round():
        # User said "none are good" -- drop the champion so the next
        # round is a fresh diverse set (no mutations of a bad champion).
        state["champion_canvas"] = None
        return redirect("/", code=303)

    @app.route("/reset", methods=["POST", "GET"])
    def reset():
        state["champion_canvas"] = None
        return redirect("/", code=303)

    @app.route("/legacy_pair")
    def legacy_index():
        """Legacy A/B pairwise UI; kept for backwards compat."""
        pair = present_pair(plan, extractor)
        if not pair.get("ok"):
            return _ERROR_HTML.format(error=pair.get("error", "unknown"))
        # Inline the SVG content (so we don't need a separate route).
        svg_a = Path(pair["svg_a_path"]).read_text(encoding="utf-8")
        svg_b = Path(pair["svg_b_path"]).read_text(encoding="utf-8")
        # Strip the outer <?xml...> if present so it embeds cleanly.
        if svg_a.startswith("<?xml"):
            svg_a = svg_a.split("?>", 1)[-1]
        if svg_b.startswith("<?xml"):
            svg_b = svg_b.split("?>", 1)[-1]
        from string import Template
        from eda_agent.design.quality import _load_quality_model
        model = _load_quality_model()
        if model:
            model_status = (
                f"learned from {model.get('n_pairs', '?')} votes "
                f"(acc {model.get('training_metrics', {}).get('accuracy', 0)*100:.0f}%)"
            )
        else:
            model_status = "heuristic (no votes trained yet)"
        return Template(_PAGE_HTML).safe_substitute(
            plan_name=plan_name,
            n_votes=_count_records(_pref_log_path()),
            model_status=model_status,
            pair_id=pair["pair_id"],
            score_a=f"{pair['score_a']:.1f}",
            score_b=f"{pair['score_b']:.1f}",
            cross_a=int(pair["features_a"]["wire_crossings"]),
            tb_a=int(pair["features_a"]["wires_through_bodies"]),
            ov_a=int(pair["features_a"]["body_overlaps"]),
            len_a=int(pair["features_a"]["total_wire_length"]),
            cross_b=int(pair["features_b"]["wire_crossings"]),
            tb_b=int(pair["features_b"]["wires_through_bodies"]),
            ov_b=int(pair["features_b"]["body_overlaps"]),
            len_b=int(pair["features_b"]["total_wire_length"]),
            svg_a=svg_a, svg_b=svg_b,
            log_path=str(_pref_log_path()),
        )

    @app.route("/vote", methods=["POST"])
    def vote():
        pair_id = request.form.get("pair_id", "").strip()
        winner = request.form.get("winner", "").strip()
        if winner == "bad":
            # "Both bad" skips the pair without recording. Useful when
            # the variants are equally garbage and a vote would only
            # confuse the model.
            return redirect("/", code=303)
        if not pair_id or winner not in ("a", "b", "tie"):
            return redirect("/", code=303)
        result = record_preference(pair_id, winner)
        if not result.get("ok"):
            logger.warning("record_preference failed: %s", result)
        return redirect("/", code=303)

    @app.route("/stats")
    def stats():
        return {
            "n_votes": _count_records(_pref_log_path()),
            "log_path": str(_pref_log_path()),
        }

    return app


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument(
        "--symbols", type=Path, default=None,
        help="Symbol fixtures JSON (offline mode). When omitted, uses the live Altium bridge.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    plan = DesignPlan.model_validate(
        json.loads(args.plan.read_text(encoding="utf-8"))
    )
    if args.symbols:
        extractor = _build_static_extractor(args.symbols)
        mode = f"offline (fixtures: {args.symbols.name})"
    else:
        extractor = _build_live_extractor()
        mode = "live (Altium bridge)"

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            f"  WARNING: binding to {args.host} exposes this server (and the "
            f"design data it serves) to the network. There is no "
            f"authentication; use 127.0.0.1 unless you mean to share it.",
        )

    app = create_app(plan, extractor, plan_name=args.plan.stem)
    print(f"\n  eda-agent vote: serving plan {args.plan.name}")
    print(f"  symbol mode:    {mode}")
    print(f"  votes logged:   {_count_records(_pref_log_path())} so far")
    print(f"  log file:       {_pref_log_path()}")
    print(f"\n  Open  http://{args.host}:{args.port}/  in your browser.")
    print(f"  Keys: A / B / T (tie) / X (both bad).\n")
    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())

# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Offline tests for the SVG->PNG rasterization helpers.

The browser screenshot itself is not exercised (no headless Edge in CI);
these cover the pure command construction, the viewBox->pixel sizing, and
the graceful fallbacks that keep the caller working when no browser
exists.
"""

from __future__ import annotations

from pathlib import Path

from eda_agent.render import rasterize as R


def test_size_svg_from_viewbox_aspect():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 400">x</svg>'
    sized, w, h = R.size_svg_for_raster(svg, target_width=1600)
    assert w == 1600
    assert h == 800  # 1600 * 400/800
    assert 'width="1600"' in sized
    assert 'height="800"' in sized


def test_size_svg_handles_negative_viewbox_origin():
    # flip_y renders use a negative y origin; aspect must use w/h only.
    svg = '<svg viewBox="100 -500 1000 250">x</svg>'
    _, w, h = R.size_svg_for_raster(svg, target_width=2000)
    assert w == 2000
    assert h == 500  # 2000 * 250/1000


def test_size_svg_without_viewbox_is_square():
    svg = "<svg>no viewbox</svg>"
    _, w, h = R.size_svg_for_raster(svg, target_width=900)
    assert w == h == 900


def test_size_svg_does_not_double_inject_width():
    svg = '<svg width="50" viewBox="0 0 800 400">x</svg>'
    sized, _, _ = R.size_svg_for_raster(svg, target_width=1600)
    assert sized.count('width="') == 1  # original width preserved, none added


def test_build_screenshot_cmd_tokens(tmp_path):
    svg = tmp_path / "x.svg"
    svg.write_text("<svg/>", encoding="utf-8")
    png = str(tmp_path / "x.png")
    cmd = R.build_screenshot_cmd("edge.exe", str(svg), png, 1600, 800)
    assert cmd[0] == "edge.exe"
    assert "--headless" in cmd
    assert f"--screenshot={png}" in cmd
    assert "--window-size=1600,800" in cmd
    assert cmd[-1].startswith("file://")


def test_find_browser_explicit_path(tmp_path):
    fake = tmp_path / "msedge.exe"
    fake.write_text("", encoding="utf-8")
    assert R.find_browser(str(fake)) == str(fake)


def test_rasterize_missing_svg_returns_error():
    out = R.rasterize_svg("does_not_exist.svg")
    assert out["ok"] is False
    assert "not found" in out["reason"]
    assert out["png_path"] is None


def test_rasterize_no_browser_falls_back(tmp_path, monkeypatch):
    svg = tmp_path / "x.svg"
    svg.write_text('<svg viewBox="0 0 10 10"/>', encoding="utf-8")
    monkeypatch.setattr(R, "find_browser", lambda explicit=None: None)
    out = R.rasterize_svg(str(svg))
    assert out["ok"] is False
    assert "no Edge/Chromium" in out["reason"]
    assert out["png_path"] is None

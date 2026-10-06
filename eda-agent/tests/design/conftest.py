# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Shared fixtures for the design-engine test suite."""

import pytest

from eda_agent.design import pipeline as _pipeline


@pytest.fixture(autouse=True)
def _fast_fd_sweep(monkeypatch):
    """Shrink the pin-aware FD attractor-stiffness sweep for test speed.

    Production sweeps ~100 values because the score-vs-stiffness landscape is
    chaotic (see ``pipeline._FD_K_SWEEP``); at 100 evals per IC board the full
    design suite runs for minutes. The tests only need the pin-aware FD path
    exercised, not its global optimum, so two values suffice here. The pin-side
    regression test restores the full sweep locally to validate the real
    production behaviour.
    """
    monkeypatch.setattr(_pipeline, "_FD_K_SWEEP", (0.06, 0.10))

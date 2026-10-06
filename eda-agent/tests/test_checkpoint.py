# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the project checkpoint/restore engine (roadmap 1.1)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from eda_agent.checkpoint import CheckpointStore


def _project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "board.PrjPcb").write_text("project", encoding="utf-8")
    (proj / "main.SchDoc").write_text("schematic v1", encoding="utf-8")
    (proj / "board.PcbDoc").write_text("pcb v1", encoding="utf-8")
    sub = proj / "libs"
    sub.mkdir()
    (sub / "parts.SchLib").write_text("lib", encoding="utf-8")
    return proj


def _store(tmp_path: Path) -> CheckpointStore:
    return CheckpointStore(tmp_path / "ckpt")


def test_create_and_restore_roundtrip(tmp_path):
    proj = _project(tmp_path)
    store = _store(tmp_path)

    info = store.create(proj, project_file="board.PrjPcb", label="before edit")
    assert info.file_count == 4
    assert info.label == "before edit"

    # Mutate the design as an AI session would.
    (proj / "main.SchDoc").write_text("schematic v2 CORRUPTED", encoding="utf-8")
    (proj / "board.PcbDoc").unlink()

    result = store.restore(info.id, proj)
    assert result["restored"] == 4
    assert not result["missing_blobs"]
    assert (proj / "main.SchDoc").read_text(encoding="utf-8") == "schematic v1"
    assert (proj / "board.PcbDoc").read_text(encoding="utf-8") == "pcb v1"


def test_blob_dedup_across_checkpoints(tmp_path):
    proj = _project(tmp_path)
    store = _store(tmp_path)

    store.create(proj, checkpoint_id="a", now=datetime(2026, 7, 2, 10, 0, 0))
    # Change one file only; the other three are byte-identical.
    (proj / "main.SchDoc").write_text("schematic v2", encoding="utf-8")
    store.create(proj, checkpoint_id="b", now=datetime(2026, 7, 2, 11, 0, 0))

    # 4 original files + 1 changed variant = 5 unique blobs, not 8.
    blob_count = len(list((store.blobs).iterdir()))
    assert blob_count == 5


def test_excluded_dirs_are_not_snapshotted(tmp_path):
    proj = _project(tmp_path)
    outputs = proj / "Project Outputs for board"
    outputs.mkdir()
    (outputs / "board.gbr").write_text("gerber", encoding="utf-8")
    (proj / ".git").mkdir()
    (proj / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    history = proj / "History"
    history.mkdir()
    (history / "old.zip").write_text("zip", encoding="utf-8")

    store = _store(tmp_path)
    info = store.create(proj)
    rels = set(info.files)
    assert "board.PrjPcb" in rels
    assert not any("Outputs" in r or ".git" in r or "History" in r for r in rels)


def test_restore_prune_added_reverts_new_files(tmp_path):
    proj = _project(tmp_path)
    store = _store(tmp_path)
    info = store.create(proj, checkpoint_id="base")

    (proj / "extra.SchDoc").write_text("added after checkpoint", encoding="utf-8")
    assert (proj / "extra.SchDoc").exists()

    result = store.restore(info.id, proj, prune_added=True)
    assert "extra.SchDoc" in result["removed"]
    assert not (proj / "extra.SchDoc").exists()


def test_restore_default_is_additive_safe(tmp_path):
    proj = _project(tmp_path)
    store = _store(tmp_path)
    info = store.create(proj, checkpoint_id="base")

    (proj / "extra.SchDoc").write_text("added after checkpoint", encoding="utf-8")
    result = store.restore(info.id, proj)  # default prune_added=False
    assert result["removed"] == []
    assert (proj / "extra.SchDoc").exists()


def test_list_is_newest_first(tmp_path):
    proj = _project(tmp_path)
    store = _store(tmp_path)
    store.create(proj, checkpoint_id="20260702-100000-000000")
    store.create(proj, checkpoint_id="20260702-110000-000000")
    ids = [c.id for c in store.list()]
    assert ids == ["20260702-110000-000000", "20260702-100000-000000"]


def test_prune_keeps_newest_and_gcs_orphan_blobs(tmp_path):
    proj = _project(tmp_path)
    store = _store(tmp_path)
    store.create(proj, checkpoint_id="c1", now=datetime(2026, 7, 2, 9, 0, 0))
    (proj / "main.SchDoc").write_text("v2", encoding="utf-8")
    store.create(proj, checkpoint_id="c2", now=datetime(2026, 7, 2, 10, 0, 0))
    (proj / "main.SchDoc").write_text("v3", encoding="utf-8")
    store.create(proj, checkpoint_id="c3", now=datetime(2026, 7, 2, 11, 0, 0))

    # Age every blob past the collector's safety window. That window
    # exists because ``create`` promotes a blob to its final name BEFORE
    # writing the manifest that references it, so for a moment a live
    # blob looks exactly like garbage; without the guard a concurrent
    # collector deletes it and the manifest points at nothing.
    #
    # Ageing them here keeps this test about the CONTRACT -- orphans are
    # collected -- rather than about how promptly, which is the
    # collector's business and changed when the guard was added.
    import os
    import time as _time

    stale = _time.time() - 3600
    for blob in store.blobs.iterdir():
        os.utime(blob, (stale, stale))

    removed = store.prune(keep=1)
    assert set(removed) == {"c1", "c2"}
    assert [c.id for c in store.list()] == ["c3"]
    # Only c3's 4 files remain as blobs; the v1/v2 SchDoc variants are GC'd.
    assert len(list(store.blobs.iterdir())) == 4


def test_a_freshly_written_blob_is_never_collected(tmp_path):
    """The guard itself: a blob mid-create must survive a collection.

    Reproduces the window directly -- a blob on disk under its final
    name with no manifest referencing it yet, which is precisely what
    ``create`` leaves behind between promoting the blob and committing
    the manifest.
    """
    store = _store(tmp_path)
    store.blobs.mkdir(parents=True, exist_ok=True)
    inflight = store.blobs / ("f" * 64)
    inflight.write_text("body being checkpointed", encoding="utf-8")

    assert store._gc_blobs() == 0
    assert inflight.exists(), (
        "the collector deleted a blob that create had not yet referenced; "
        "its manifest would point at nothing and the restore would fail")


def test_restore_unknown_checkpoint_raises(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(FileNotFoundError):
        store.restore("nope", tmp_path)


def test_max_file_bytes_skips_oversize(tmp_path):
    proj = _project(tmp_path)
    (proj / "huge.bin").write_bytes(b"x" * 2048)
    store = CheckpointStore(tmp_path / "ckpt", max_file_bytes=1024)
    info = store.create(proj)
    assert "huge.bin" not in info.files
    assert "huge.bin" in info.skipped_large
    assert "board.PrjPcb" in info.files


def test_prune_added_keeps_oversize_skipped_file(tmp_path):
    # An oversize file existed at checkpoint time but was too big to snapshot.
    # A prune_added revert must NOT delete it (it is known, not added-since) --
    # otherwise reverting would silently destroy a large .PcbLib / 3D model.
    proj = _project(tmp_path)
    huge = proj / "models.PcbLib"
    huge.write_bytes(b"x" * 4096)
    store = CheckpointStore(tmp_path / "ckpt", max_file_bytes=1024)
    info = store.create(proj)
    assert "models.PcbLib" in info.skipped_large

    result = store.restore(info.id, proj, prune_added=True)
    assert "models.PcbLib" not in result["removed"]
    assert huge.exists(), "oversize file was silently deleted on revert"


def test_manifest_without_skipped_large_field_loads(tmp_path):
    # Backward compat: a manifest written before skipped_large existed must
    # still load (the field defaults to an empty list).
    store = CheckpointStore(tmp_path / "ckpt")
    store._ensure_dirs()
    legacy = {
        "id": "legacy", "created": "2026-07-01T10:00:00", "label": "",
        "project_file": "", "file_count": 0, "total_bytes": 0, "files": {},
    }
    (store.manifests / "legacy.json").write_text(json.dumps(legacy),
                                                 encoding="utf-8")
    info = store._load("legacy")
    assert info.skipped_large == []

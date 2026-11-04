"""`subtask du` reports the space on disk, not the sum of every file name (T-7465)."""

from __future__ import annotations

import os


def test_hard_links_count_once(tmp_path):
    from subtask import gc

    (tmp_path / "a").write_bytes(b"x" * 8192)
    os.link(tmp_path / "a", tmp_path / "b")

    alone = (tmp_path / "a").stat().st_blocks * 512
    assert gc._size(tmp_path) == alone


def test_sparse_file_counts_its_real_blocks(tmp_path):
    from subtask import gc

    sparse = tmp_path / "sparse"
    with sparse.open("wb") as handle:
        handle.truncate(10**9)

    assert gc._size(tmp_path) == sparse.stat().st_blocks * 512
    assert gc._size(tmp_path) < 10**9


def test_symlink_target_is_not_counted(tmp_path):
    from subtask import gc

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big").write_bytes(b"x" * 8192)
    inside = tmp_path / "inside"
    inside.mkdir()
    (inside / "link").symlink_to(outside / "big")

    assert gc._size(inside) < 8192

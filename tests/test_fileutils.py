"""Tests for fileutils.py - the shared archive/sanitize helpers used by the
lineage pipeline worker and the Dataflow Export browser download handler."""
import os

import pytest

from services import fileutils


@pytest.mark.parametrize("raw, expected", [
    ("MyDataflow", "MyDataflow"),
    ("My:Dataflow*Name?", "My_Dataflow_Name_"),
    ("  spaced.out.  ", "spaced.out"),
    ("", "export"),
    ("...", "export"),
    ("a/b\\c", "a_b_c"),
])
def test_sanitize_filename(raw, expected):
    assert fileutils.sanitize_filename(raw) == expected


def test_archive_if_exists_moves_file(tmp_path):
    output_folder = str(tmp_path)
    target = os.path.join(output_folder, "MyDataflow.json")
    with open(target, "w", encoding="utf-8") as f:
        f.write("old content")

    fileutils.archive_if_exists(target, output_folder)

    assert not os.path.exists(target)
    previous_runs = os.path.join(output_folder, "previous_runs")
    assert os.path.isdir(previous_runs)
    stamps = os.listdir(previous_runs)
    assert len(stamps) == 1
    archived_file = os.path.join(previous_runs, stamps[0], "MyDataflow.json")
    assert os.path.exists(archived_file)
    with open(archived_file, encoding="utf-8") as f:
        assert f.read() == "old content"


def test_archive_if_exists_noop_when_missing(tmp_path):
    output_folder = str(tmp_path)
    target = os.path.join(output_folder, "does_not_exist.json")

    fileutils.archive_if_exists(target, output_folder)

    assert not os.path.exists(os.path.join(output_folder, "previous_runs"))


def test_archive_if_exists_disabled(tmp_path):
    output_folder = str(tmp_path)
    target = os.path.join(output_folder, "MyDataflow.json")
    with open(target, "w", encoding="utf-8") as f:
        f.write("kept")

    fileutils.archive_if_exists(target, output_folder, archive_previous=False)

    assert os.path.exists(target)
    assert not os.path.exists(os.path.join(output_folder, "previous_runs"))

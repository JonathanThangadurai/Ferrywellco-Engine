"""Tests for the keys.env parser (fake values only)."""

import os

from gotime_data import keys


def test_parse_env_file(tmp_path):
    f = tmp_path / "keys.env"
    f.write_text("# comment\n\nA_KEY=abc\nB_URL = 'https://x/api'\nnot a line\n")
    assert keys.parse_env_file(f) == {"A_KEY": "abc", "B_URL": "https://x/api"}


def test_environment_wins_over_file(tmp_path, monkeypatch):
    f = tmp_path / "keys.env"
    f.write_text("GOTIME_TEST_KEY=from_file\n")
    monkeypatch.setenv("GOTIME_TEST_KEY", "from_env")
    keys.load_keys(f)
    assert os.environ["GOTIME_TEST_KEY"] == "from_env"


def test_missing_file_is_empty(tmp_path):
    assert keys.parse_env_file(tmp_path / "nope.env") == {}

"""Shared test setup: a temporary storage root and access to the saved fixtures (no network)."""

import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def storage(tmp_path, monkeypatch):
    """Point GOTIME_STORAGE_ROOT at an empty temporary folder."""
    monkeypatch.setenv("GOTIME_STORAGE_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture
def raw_batch(storage):
    """Copy one fixture batch into the temporary raw folder and return its path."""

    def copy(source: str) -> Path:
        src = next(p for p in (FIXTURES / source).iterdir() if p.is_dir())
        dst = storage / "raw" / source / src.name
        shutil.copytree(src, dst)
        (storage / "static").mkdir(exist_ok=True)
        for f in (FIXTURES / "static").iterdir():
            shutil.copy(f, storage / "static" / f.name)
        return dst

    return copy

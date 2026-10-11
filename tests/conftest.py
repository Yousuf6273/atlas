"""Shared fixtures.

All companies, websites and people in tests/fixtures/synthetic are SYNTHETIC:
they use reserved example domains (RFC 2606 ``.example`` / ``.test``) and
fictional names. They exist only to exercise the code and must never be shown
as prospecting results.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from atlas.config import AppConfig, PathSettings
from atlas.db.core import open_db

FIXTURES = Path(__file__).parent / "fixtures"
SYNTHETIC = FIXTURES / "synthetic"


def pytest_collection_modifyitems(config, items):
    if os.environ.get("ATLAS_LIVE_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="live test: set ATLAS_LIVE_TESTS=1 to run against real services")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def cfg(tmp_path: Path) -> AppConfig:
    c = AppConfig(paths=PathSettings(data_dir=tmp_path / "data"))
    c.http.per_domain_min_interval_s = 0.5
    c.http.max_retries = 2
    c.http.backoff_base_s = 0.01
    c.http.backoff_max_s = 0.05
    c.ensure_dirs()
    return c


@pytest.fixture
def conn(cfg: AppConfig):
    c = open_db(cfg.paths.db_path)
    yield c
    c.close()


def synthetic_html(name: str) -> str:
    return (SYNTHETIC / "sites" / name).read_text(encoding="utf-8")

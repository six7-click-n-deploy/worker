"""Pytest configuration and fixtures."""

import shutil
import tempfile
from pathlib import Path

import pytest


# ----------------------------------------------------------------
# Auto-marker policy
# ----------------------------------------------------------------
# CI rennt zwei getrennte Lanes: ``pytest -m unit`` und
# ``pytest -m integration``. Tests, die *gar keinen* Marker tragen,
# fallen aus **beiden** Selektoren raus — d.h. sie würden lautlos
# nicht in CI ausgeführt, obwohl sie lokal grün sind. Genau das ist
# uns hier passiert: 12 von 23 Tests waren unmarked und wurden nie
# durch die Pipeline laufen.
#
# Diese Hook setzt für jeden Test ohne ``integration``/``slow``-
# Marker implizit ``unit``. Damit gilt: **default ist unit**, und
# nur Tests, die echte externe Abhängigkeiten brauchen (DB, Broker,
# Netz), müssen explizit als ``integration`` markiert werden.
def pytest_collection_modifyitems(config, items):
    for item in items:
        markers = {m.name for m in item.iter_markers()}
        # ``integration`` / ``slow`` haben Vorrang — wer explizit
        # markiert, will nicht in die unit-Lane gezogen werden.
        if "integration" not in markers and "slow" not in markers and "unit" not in markers:
            item.add_marker(pytest.mark.unit)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    temp_path = Path(tempfile.mkdtemp())
    yield temp_path
    if temp_path.exists():
        shutil.rmtree(temp_path)


@pytest.fixture
def mock_git_url():
    """Mock Git URL for testing."""
    return "https://github.com/test-org/test-repo.git"


@pytest.fixture
def mock_tag():
    """Mock Git tag for testing."""
    return "v1.0.0"

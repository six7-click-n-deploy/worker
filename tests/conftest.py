"""Pytest configuration and fixtures."""

import pytest
from pathlib import Path
import tempfile
import shutil


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

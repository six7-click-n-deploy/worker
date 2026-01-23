"""Tests for configuration."""

import pytest
from unittest.mock import patch
import os


@pytest.mark.unit
class TestConfiguration:
    """Test configuration loading."""
    
    def test_settings_import(self):
        """Test that settings can be imported."""
        from app.config import settings
        
        assert settings is not None
    
    def test_required_settings_exist(self):
        """Test that required settings attributes exist."""
        from app.config import settings
        
        assert hasattr(settings, 'DATABASE_URL')
        assert hasattr(settings, 'CELERY_BROKER_URL')
        assert hasattr(settings, 'TEMP_REPO_BASE_PATH')
        assert hasattr(settings, 'GIT_ACCESS_TOKEN')
    
    @patch.dict(os.environ, {
        'DATABASE_URL': 'postgresql://test:test@localhost/test',
        'CELERY_BROKER_URL': 'amqp://test@localhost',
        'GIT_ACCESS_TOKEN': 'test-token',
    }, clear=True)
    def test_settings_from_environment(self):
        """Test loading settings from environment variables."""
        # Reload settings with new environment
        from importlib import reload
        from app import config
        reload(config)
        
        assert 'postgresql' in config.settings.DATABASE_URL
        assert 'amqp' in config.settings.CELERY_BROKER_URL
        assert config.settings.GIT_ACCESS_TOKEN == 'test-token'

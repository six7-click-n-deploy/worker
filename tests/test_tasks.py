"""Tests for Celery tasks."""

import pytest


@pytest.mark.unit
class TestTaskBasics:
    """Basic task tests."""

    def test_task_import(self):
        """Test that tasks can be imported."""
        from app.tasks import deploy_app, destroy_app

        assert deploy_app is not None
        assert destroy_app is not None

    def test_celery_app_import(self):
        """Test that celery app can be imported."""
        from app.celery_app import celery_app

        assert celery_app is not None
        assert celery_app.conf.broker_url is not None

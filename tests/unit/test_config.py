"""Unit tests for core configuration."""
import os

import pytest


def test_settings_has_required_fields():
    """Settings object exposes all required configuration fields."""
    os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://u:p@localhost/test")
    from core.config import settings

    assert hasattr(settings, "database_url")
    assert hasattr(settings, "tupi_base_url")
    assert hasattr(settings, "poll_interval")
    assert hasattr(settings, "log_level")


def test_poll_interval_is_positive():
    os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://u:p@localhost/test")
    from core.config import settings

    assert settings.poll_interval > 0

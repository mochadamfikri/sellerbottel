"""Regression tests for server startup configuration validation."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_startup_rejects_production_mongo_url_in_development_before_db_init(monkeypatch):
    """Startup rejects a production MongoDB endpoint before database initialization."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("MONGO_URL", "mongodb://localhost:27017/sellerbottel_dev")
    monkeypatch.setenv("DB_NAME", "sellerbottel_dev")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret")

    import server

    with patch.object(server, "ensure_settings", new_callable=AsyncMock) as ensure_settings:
        with patch.object(server, "ensure_indexes", new_callable=AsyncMock) as ensure_indexes:
            with pytest.raises(ValueError, match="27017"):
                asyncio.run(server.startup())

    ensure_settings.assert_not_awaited()
    ensure_indexes.assert_not_awaited()

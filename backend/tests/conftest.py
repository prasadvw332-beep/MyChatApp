"""Shared fixtures for all tests.

    tests/unit/  functions and modules called directly
    tests/api/   the HTTP contract, through FastAPI's TestClient

Neither needs Docker or running services: tests/fakes.py stands in for Qdrant, MinIO, Ollama and
PostgreSQL, and the app's lifespan (migrations, Qdrant sync) is never started.
"""

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app.api import deps
from app.main import app
from app.models import User


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)  # no `with`: the lifespan doesn't run


@pytest.fixture
def logged_in():
    """Every request is made as user 1, without a session cookie or a database."""
    user = User(id=1, username="tester")
    app.dependency_overrides[deps.current_user] = lambda: user
    yield user
    app.dependency_overrides.clear()


@pytest.fixture
def metric():
    """metric("http_requests_total", route="/health") -> the counter's current value (0 if never set)."""
    return lambda name, **labels: REGISTRY.get_sample_value(name, labels) or 0.0

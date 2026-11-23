"""Pytest fixtures. Each test module gets a clean SQLite database."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator

import pytest

TMP_DB = os.path.join(tempfile.mkdtemp(prefix="aurex-test-"), "test.db")
os.environ.update(
    {
        "DATABASE_URL": f"sqlite:///{TMP_DB}",
        "ENVIRONMENT": "test",
        "SECRET_KEY": "test-secret-key-for-aurex-ai-suite-0123456789abcdef",
        "FIRST_ADMIN_EMAIL": "admin@aurex.ai",
        "FIRST_ADMIN_PASSWORD": "ChangeMe!2026",
        "DEFAULT_PAPER_BALANCE": "100000",
    }
)

from fastapi.testclient import TestClient  # noqa: E402

from app.core.rate_limit import reset_rate_limits  # noqa: E402
from app.database.session import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402  (FastAPI instance)
import app.models as _models  # noqa: E402,F401  (registers mappers)
from app.models import User, UserRole  # noqa: E402
from app.services import auth_service  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Generator[None, None, None]:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def _clean_tables() -> Generator[None, None, None]:
    """Truncate every table between tests for full isolation."""
    reset_rate_limits()
    yield
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def user_factory():
    def _create(email: str = "trader@aurex.ai", password: str = "Str0ngPass!", role: UserRole = UserRole.USER):
        db = SessionLocal()
        try:
            user = auth_service.register(
                db, email=email, full_name="Test Trader", password=password, role=role
            )
            db.commit()
            db.refresh(user)
            db.expunge(user)
            return user
        finally:
            db.close()

    return _create


@pytest.fixture
def auth_headers(client: TestClient, user_factory):
    def _login(email: str = "trader@aurex.ai", password: str = "Str0ngPass!") -> dict[str, str]:
        try:
            user_factory(email, password)
        except Exception:
            pass  # already registered
        response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _login


@pytest.fixture
def admin_headers(client: TestClient, user_factory):
    def _login() -> dict[str, str]:
        try:
            user_factory("root@aurex.ai", "Adm1nPass!", UserRole.ADMIN)
        except Exception:
            pass
        response = client.post(
            "/api/v1/auth/login", json={"email": "root@aurex.ai", "password": "Adm1nPass!"}
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _login

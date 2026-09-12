import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import Base, get_db
from app.main import app
from app.models.models import Role, User
from app.seed import seed

engine = create_engine(settings.database_url, pool_pre_ping=True)
TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture(autouse=True)
def _reset_db():
    """Fresh schema per test — this milestone's suite is small enough that
    correctness (a real Postgres, no leaked state) matters more than speed."""
    Base.metadata.drop_all(bind=engine)
    with engine.begin() as conn:
        # Milestone 15 (search): create_all doesn't run raw-SQL migrations,
        # so the pg_trgm extension the search router's trigram matching
        # depends on needs enabling here too (indexes themselves aren't
        # required for tests to pass, just the extension/operator).
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session():
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def seeded(db_session):
    seed(db_session)
    return db_session


def make_user(db_session, username: str, role_name: str, password: str = "pw123456") -> User:
    role = db_session.query(Role).filter(Role.name == role_name).one_or_none()
    if role is None:
        seed(db_session)
        role = db_session.query(Role).filter(Role.name == role_name).one()
    user = User(
        username=username,
        display_name=username,
        password_hash=hash_password(password),
        roles=[role],
    )
    db_session.add(user)
    db_session.commit()
    return user


def auth_headers(client, username: str, password: str = "pw123456") -> dict:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

from fastapi.testclient import TestClient

from app.db.session import get_session
from main import app


class BrokenSession:
    async def execute(self, *args, **kwargs):
        raise OSError("sem ligação")


async def broken_session():
    yield BrokenSession()


def test_database_health_is_ok_when_the_database_answers(database):
    response= TestClient(app).get("/health/db")

    assert response.status_code== 200
    assert response.json()== {"status": "ok"}


def test_database_health_is_503_when_the_database_is_down():
    app.dependency_overrides[get_session]= broken_session
    try:
        response= TestClient(app).get("/health/db")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code== 503


def test_plain_health_does_not_need_the_database():
    assert TestClient(app).get("/health").json()== {"status": "ok"}

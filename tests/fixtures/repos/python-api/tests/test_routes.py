from fastapi.testclient import TestClient

from app.main import app


def test_tickets() -> None:
    assert TestClient(app).get("/events/1/tickets").status_code == 200

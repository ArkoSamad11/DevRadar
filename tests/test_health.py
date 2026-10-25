from fastapi.testclient import TestClient

from devradar.api.main import app


def test_health_reports_database_ok():
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}

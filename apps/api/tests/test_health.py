from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.main import create_app


def make_app():
    return create_app(Settings(environment=Environment.TEST, _env_file=None))


def test_health_does_not_require_lifespan_readiness() -> None:
    app = make_app()
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    client.close()


def test_ready_requires_lifespan() -> None:
    app = make_app()
    client = TestClient(app)

    response = client.get("/ready")

    assert response.status_code == 503
    client.close()


def test_lifespan_marks_the_app_ready_and_cleans_up() -> None:
    app = make_app()
    assert app.state.ready is False


def test_not_ready_uses_uniform_error() -> None:
    app = make_app()
    client = TestClient(app)
    response = client.get("/ready", headers={"X-Request-ID": "not-ready"})
    assert response.status_code == 503
    assert response.json() == {"error": {"code": "not_ready", "message": "服务尚未就绪", "request_id": "not-ready", "details": []}}
    client.close()

    with TestClient(app) as client:
        assert app.state.ready is True
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}

    assert app.state.ready is False

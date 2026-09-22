from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.exception_handler import http_exception_handler


def test_http_exception_handler_preserves_protocol_headers() -> None:
    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)

    @app.get("/git-auth-challenge")
    def challenge() -> None:
        raise HTTPException(
            status_code=401,
            detail="Invalid Git credentials",
            headers={
                "WWW-Authenticate": 'Basic realm="PuppyOne Git"',
                "Retry-After": "5",
            },
        )

    response = TestClient(app).get("/git-auth-challenge")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Basic realm="PuppyOne Git"'
    assert response.headers["retry-after"] == "5"
    assert response.json()["message"] == "Invalid Git credentials"

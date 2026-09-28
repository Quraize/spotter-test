import pytest
from django.urls import reverse
from rest_framework.test import APIClient


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def test_health_returns_ok(client: APIClient) -> None:
    response = client.get(reverse("api:health"))

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["django"].startswith("6.1")


def test_openapi_schema_is_served(client: APIClient) -> None:
    response = client.get(reverse("schema"))

    assert response.status_code == 200

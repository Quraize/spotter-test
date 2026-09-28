"""Map page tests: server-rendered Leaflet page built from the same plan as the API."""

from __future__ import annotations

import json

from django.urls import reverse
from rest_framework.test import APIClient

from apps.routing.base import ProviderTimeout

MAP = reverse("api:route-plan-map")
API = reverse("api:route-plan")


def test_empty_map_page_renders_form_only(client: APIClient) -> None:
    response = client.get(MAP)

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")
    html = response.content.decode()
    assert 'name="start"' in html
    assert '<script id="plan-data"' not in html
    assert "leaflet@1.9.4" in html
    assert "basemaps.cartocdn.com" in html


def test_map_page_embeds_plan_and_stops(client: APIClient, stubs) -> None:
    response = client.get(MAP, {"start": "Start, IL", "finish": "Finish, OH"})

    assert response.status_code == 200
    html = response.content.decode()
    assert "STATION 1" in html and "STATION 2" in html
    assert "Before departure" in html  # pre-trip purchase row
    assert 'value="Start, IL"' in html  # form is prefilled

    # The embedded JSON is the API payload, safely escaped via json_script.
    start = html.index('<script id="plan-data" type="application/json">') + len(
        '<script id="plan-data" type="application/json">'
    )
    end = html.index("</script>", start)
    plan = json.loads(html[start:end])
    api = client.get(API, {"start": "Start, IL", "finish": "Finish, OH"}).json()
    for key in ("start", "finish", "fuel_stops", "summary", "pre_trip_purchase"):
        assert plan[key] == api[key]
    assert plan["route"]["geometry"]["type"] == "LineString"


def test_map_page_makes_no_extra_upstream_calls(client: APIClient, stubs) -> None:
    _, router, _ = stubs
    client.get(API, {"start": "Start, IL", "finish": "Finish, OH"})
    assert router.calls == 1

    client.get(MAP, {"start": "Start, IL", "finish": "Finish, OH"})

    assert router.calls == 1  # served from the route cache


def test_api_response_links_to_map_with_same_parameters(client: APIClient, stubs) -> None:
    body = client.get(API, {"start": "Start, IL", "finish": "Finish, OH", "stop_penalty": 3}).json()

    assert body["map_url"].startswith("http://testserver" + MAP)
    assert "start=Start%2C+IL" in body["map_url"]
    assert "stop_penalty=3" in body["map_url"]
    assert "corridor_miles=10" in body["map_url"]

    follow = client.get(body["map_url"])
    assert follow.status_code == 200
    assert "STATION" in follow.content.decode()


def test_map_page_shows_validation_errors(client: APIClient, stubs) -> None:
    response = client.get(MAP, {"start": "Start, IL", "finish": "Finish, OH", "corridor_miles": 99})

    assert response.status_code == 400
    html = response.content.decode()
    assert "Invalid request." in html
    assert "corridor_miles" in html
    assert '<script id="plan-data"' not in html


def test_map_page_shows_domain_errors_with_status(client: APIClient, stubs) -> None:
    response = client.get(MAP, {"start": "Atlantis", "finish": "Finish, OH"})

    assert response.status_code == 422
    assert "Could not resolve &#x27;Atlantis&#x27;" in response.content.decode()


def test_map_page_shows_upstream_errors(client: APIClient, stubs) -> None:
    _, router, _ = stubs
    router.fail = ProviderTimeout("ors: timed out")

    response = client.get(MAP, {"start": "Start, IL", "finish": "Finish, OH"})

    assert response.status_code == 504
    assert "timed out" in response.content.decode()


def test_map_page_escapes_user_input(client: APIClient, stubs) -> None:
    evil = '</script><script>alert(1)</script>"><img src=x onerror=alert(1)>'

    response = client.get(MAP, {"start": evil, "finish": "Finish, OH"})

    html = response.content.decode()
    assert response.status_code == 422
    assert "<script>alert(1)</script>" not in html
    assert "<img" not in html  # the tag never appears raw...
    assert "&lt;img src=x onerror=alert(1)&gt;" in html  # ...only escaped, as inert text

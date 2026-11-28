from datetime import date, timedelta
import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError

from auth import digest, get_auth_store, token_key
from local_auth_store import LocalAuthStore
from main import app, get_agent


@pytest.fixture
def auth_store(monkeypatch):
    monkeypatch.setenv("TRAVEL_CHAT_RATE_LIMIT", "10")
    monkeypatch.setenv("TRAVEL_SEARCH_RATE_LIMIT", "30")
    owner = digest("alice")
    values = {token_key("alice-token"): owner, f"travel:session:{owner}:trip-1": "1"}
    counts = {}
    store = Mock()
    store.get.side_effect = values.get

    def save(key, value, *, ex, nx=False):
        if nx and key in values:
            return False
        values[key] = value
        return True

    def count(_script, _keys, key, window):
        counts[key] = counts.get(key, 0) + 1
        return counts[key], window

    store.set.side_effect = save
    store.expire.side_effect = lambda key, ttl: key in values
    store.delete.side_effect = lambda key: values.pop(key, None)
    store.eval.side_effect = count
    monkeypatch.setitem(app.dependency_overrides, get_auth_store, lambda: store)
    return store


@pytest.fixture
def client(auth_store):
    with TestClient(app, headers={"Authorization": "Bearer alice-token"}) as client:
        yield client


@pytest.fixture
def agent_factory(monkeypatch):
    factory = Mock()
    monkeypatch.setattr("main.get_agent", factory)
    return factory


def test_health_does_not_initialize_agent(client, agent_factory):
    assert client.get("/health").json() == {"status": "ok"}
    agent_factory.assert_not_called()


def test_local_chat_follow_up_retains_flight_fare(client, monkeypatch, tools):
    store = LocalAuthStore()
    store.set(token_key("alice-token"), digest("alice"), ex=60)
    monkeypatch.setitem(app.dependency_overrides, get_auth_store, lambda: store)
    monkeypatch.setattr("main.get_auth_store", lambda: store)
    monkeypatch.setattr("main.get_tools", lambda: tools)
    bedrock = Mock()
    monkeypatch.setattr("agent.boto3.client", Mock(return_value=bedrock))
    monkeypatch.setattr("main.get_agent", get_agent.__wrapped__)
    departure = (date.today() + timedelta(days=1)).isoformat()
    search_result = {
        "source": "Hyderabad", "destination": "Goa", "departure_date": departure,
        "currency": "INR", "trip_type": "one_way", "search_mode": "one_way", "travellers": 2,
        "prices_include_all_travellers": True, "options": [{"price": 8400}],
    }
    search = Mock(return_value=search_result)
    monkeypatch.setattr(tools, "search_flights", search)
    bedrock.converse.side_effect = [
        {"stopReason": "tool_use", "output": {"message": {"role": "assistant", "content": [
            {"toolUse": {"toolUseId": "flight", "name": "search_flights", "input": {
                "origin": "HYD", "destination": "GOI", "departure_date": departure, "adults": 2,
            }}},
        ]}}},
        {"stopReason": "end_turn", "output": {"message": {"role": "assistant", "content": [
            {"text": "I found a flight."},
        ]}}},
        {"stopReason": "end_turn", "output": {"message": {"role": "assistant", "content": [
            {"text": "The saved fare is ₹4,200 per person."},
        ]}}},
    ]
    first = client.post("/api/chat", json={"message": "Find flights from Hyderabad to Goa."})
    assert first.status_code == 200
    follow_up = client.post("/api/chat", json={
        "session_id": first.json()["session_id"], "message": "What is the flight cost per person?",
    })
    assert follow_up.status_code == 200
    messages = bedrock.converse.call_args.kwargs["messages"]
    context = next(
        block["text"] for message in messages for block in message["content"]
        if "Previous flight search: " in block.get("text", "")
    )
    assert json.loads(context.split("Previous flight search: ", 1)[1].splitlines()[0]) == search_result
    search.assert_called_once()


@pytest.mark.parametrize(
    ("mode", "redis_down", "missing_provider", "bad_accounts", "expected"),
    [
        ("redis", False, False, False, 200),
        ("redis", True, False, False, 503),
        ("redis", False, True, False, 503),
        ("redis", False, False, True, 503),
        ("memory", False, False, False, 200),
    ],
)
def test_readiness_checks_dependencies(
    client, agent_factory, auth_store, monkeypatch, tmp_path,
    mode, redis_down, missing_provider, bad_accounts, expected,
):
    monkeypatch.setenv("TRAVEL_AUTH_STORE", mode)
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-model")
    monkeypatch.setenv("APIFY_TOKEN", "" if missing_provider else "private-test-token")
    monkeypatch.setenv("SERPAPI_KEY", "private-test-token")
    monkeypatch.setenv("GEOAPIFY_KEY", "private-test-token")
    path = tmp_path / "login.json"
    monkeypatch.setenv("TRAVEL_ACCOUNTS_FILE", str(path))
    if bad_accounts:
        path.write_text("invalid JSON", encoding="utf-8")
    monkeypatch.setattr("main.get_auth_store", lambda: auth_store)
    auth_store.ping.return_value = True
    if redis_down:
        auth_store.ping.side_effect = ConnectionError("private-redis-host")

    response = client.get("/ready", headers={"Authorization": ""})
    assert response.status_code == expected
    assert response.json() == {
        "status": "ready" if expected == 200 else "not_ready",
        "checks": {
            "provider_configuration": "unavailable" if missing_provider else "ok",
            "accounts": "unavailable" if bad_accounts else "ok",
            "redis": "skipped_local_mode" if mode == "memory" else "unavailable" if redis_down else "ok",
        },
    }
    assert response.headers["Cache-Control"] == "no-store"
    assert client.get("/health").status_code == 200
    assert auth_store.ping.call_count == (0 if mode == "memory" else 1)
    agent_factory.assert_not_called()


def test_city_suggestions_use_indian_airport_data(client, agent_factory, tools, monkeypatch):
    monkeypatch.setattr("main.get_tools", lambda: tools)
    result = client.get("/api/cities")
    assert result.status_code == 200
    cities = result.json()
    assert {"New Delhi", "Hyderabad", "Chennai"} <= set(cities)
    assert "London" not in cities
    assert cities == sorted(set(cities), key=str.casefold)
    agent_factory.assert_not_called()


def test_chat_returns_structured_partial_results(client, agent_factory):
    result = {
        "answer": "Flights are available.",
        "results": {"flights": {
            "source": "Delhi", "destination": "Goa", "trip_type": "round_trip",
            "search_mode": "round_trip", "currency": "INR",
            "departure_date": "2030-01-01", "return_date": "2030-01-03",
            "options": [{
                "price": 2500, "segments": [{"airline": "IndiGo"}],
                "return_options": [{"price": 2500, "segments": [{"airline": "IndiGo"}]}],
            }],
        }, "hotels": None},
        "notices": ["Hotel information is temporarily unavailable."],
        "budget": {"missing_costs": ["hotels"]},
        "outcome": "partial",
    }
    agent = agent_factory.return_value
    agent.chat_with_results.return_value = result
    start = date.today() + timedelta(days=1)
    end = start + timedelta(days=3)
    response = client.post("/api/chat", json={
        "message": "Plan a trip", "session_id": "trip-1",
        "trip": {
            "source": "Delhi", "destination": "Goa", "trip_type": "round",
            "travellers": 2, "budget": 30000,
            "start_date": start.isoformat(), "end_date": end.isoformat(),
        },
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == result["answer"]
    assert payload["results"]["hotels"] is None
    flights = payload["results"]["flights"]
    assert flights["outbound"]["source"] == "Delhi"
    assert flights["return"]["source"] == "Goa"
    assert flights["return"]["options"][0]["outbound_id"] == 1
    for leg in ("outbound", "return"):
        option = flights[leg]["options"][0]
        assert option["airline_website"] == "https://www.goindigo.in/"
        assert option["segments"][0]["airline_website"] == option["airline_website"]
    assert flights["return"]["options"][0]["search_link"].startswith("https://")
    message, session = agent.chat_with_results.call_args.args
    assert agent.chat_with_results.call_args.kwargs["budget_inputs"] == {
        "travellers": 2, "nights": (end - start).days, "total_budget": 30000,
    }
    assert session == f"{digest('alice')}:trip-1"
    assert payload["session_id"] == "trip-1"
    assert "2 travellers" in message and end.isoformat() in message


@pytest.mark.parametrize("change", [
    {"travellers": 0},
    {"travellers": 1.5},
    {"start_date": "invalid"},
    {"start_date": "2000-01-01"},
    {"end_date": None},
    {"end_date": "2000-01-01"},
    {"trip_type": "oneway"},
    {"destination": "delhi"},
])
def test_invalid_trip_does_not_initialize_agent(client, agent_factory, change):
    start = date.today() + timedelta(days=1)
    trip = {
        "source": "Delhi", "destination": "Goa", "trip_type": "round",
        "travellers": 2, "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=3)).isoformat(),
    }
    response = client.post("/api/chat", json={
        "message": "Plan a trip", "session_id": "trip-1", "trip": {**trip, **change},
    })
    assert response.status_code == 422
    agent_factory.assert_not_called()


def test_chat_handles_agent_startup_failure(client, agent_factory):
    agent_factory.side_effect = RuntimeError("private configuration")
    response = client.post("/api/chat", json={"message": "Hello", "session_id": "trip-1"})
    assert response.status_code == 503
    assert "private configuration" not in response.text


def test_signup_login_and_logout(client, monkeypatch, agent_factory, tmp_path):
    account_file = tmp_path / "login.json"
    monkeypatch.setenv("TRAVEL_ACCOUNTS_FILE", str(account_file))
    store = LocalAuthStore()
    store.set(f"travel:session:{digest('alice')}:trip-1", "1", ex=60)
    monkeypatch.setitem(app.dependency_overrides, get_auth_store, lambda: store)
    payload = {"message": "Hello", "session_id": "trip-1"}
    assert client.post("/api/chat", json=payload, headers={"Authorization": ""}).status_code == 401
    for endpoint in ("flights", "hotels", "restaurants"):
        denied = client.post(f"/api/{endpoint}", json={}, headers={"Authorization": "Bearer invalid"})
        assert denied.status_code == 401
    credentials = {"username": "Alice", "password": "test password"}
    assert client.post("/api/auth/login", json=credentials).status_code == 401
    invalid = client.post("/api/auth/signup", json={**credentials, "password": "too short"})
    assert invalid.status_code == 422
    assert "too short" not in invalid.text
    assert client.post("/api/auth/signup", json=credentials).status_code == 201
    assert client.post("/api/auth/signup", json={**credentials, "username": "alice"}).status_code == 409
    assert "test password" not in account_file.read_text(encoding="utf-8")
    assert client.post("/api/auth/login", json={"username": "alice", "password": "wrong"}).status_code == 401
    result = client.post("/api/auth/login", json={"username": "alice", "password": "test password"})
    assert result.status_code == 200
    headers = {"Authorization": f"Bearer {result.json()['access_token']}"}
    agent_factory.return_value.chat_with_results.return_value = {
        "answer": "Hello", "results": {}, "notices": [], "budget": None, "outcome": "success",
    }
    assert client.post("/api/chat", json=payload, headers=headers).status_code == 200
    assert client.post("/api/auth/logout", headers=headers).status_code == 204
    assert client.post("/api/chat", json=payload, headers=headers).status_code == 401


def test_session_ownership_and_chat_rate_limit(client, auth_store, agent_factory):
    auth_store.set(token_key("bob-token"), digest("bob"), ex=3600)
    agent = agent_factory.return_value
    agent.chat_with_results.return_value = {
        "answer": "Hello", "results": {}, "notices": [], "budget": None, "outcome": "success",
    }
    first = client.post("/api/chat", json={"message": "Start my trip"})
    assert first.status_code == 200
    session = first.json()["session_id"]
    payload = {"message": "What about the hotel?", "session_id": session}
    assert client.post("/api/chat", json=payload, headers={"Authorization": "Bearer bob-token"}).status_code == 404
    for _ in range(9):
        assert client.post("/api/chat", json=payload).status_code == 200
    denied = client.post("/api/chat", json=payload)
    assert denied.status_code == 429
    assert denied.headers["Retry-After"] == "60"
    assert agent.chat_with_results.call_count == 10
    assert agent.chat_with_results.call_args.args[1] == f"{digest('alice')}:{session}"


def test_individual_searches_skip_bedrock(client, agent_factory, tools, http_get, http_post, response, monkeypatch):
    monkeypatch.setattr("main.get_tools", lambda: tools)
    http_post.return_value = response([
        {"price": 2500, "currency": "INR", "segments": [
            {"airline": "IndiGo", "departureAirport": "MAA", "arrivalAirport": "HYD"},
        ]},
        {"price": 3000, "currency": "INR", "segments": [
            {"airline": "Air India", "departureAirport": "MAA", "arrivalAirport": "BLR"},
            {"airline": "Air India Express", "departureAirport": "BLR", "arrivalAirport": "HYD"},
        ]},
        {"price": 4000, "currency": "INR", "segments": [
            {"airline": "Unknown carrier", "departureAirport": "MAA", "arrivalAirport": "HYD"},
        ]},
    ])
    http_get.side_effect = [
        response({"properties": [
            {"name": "Test Hotel", "link": "javascript:alert(1)"},
            {"name": "Property Website", "link": "https://hotel.example/"},
        ]}),
        response({"results": [{"lat": 17, "lon": 78}]}),
        response({"features": [{"properties": {"name": "Test Restaurant"}}]}),
    ]
    start = date.today() + timedelta(days=1)
    flights = client.post("/api/flights", json={
        "source": "MAA", "destination": "HYD", "trip_type": "oneway",
        "start_date": start.isoformat(), "travellers": 1,
    }).json()
    assert flights["return"] is None
    assert flights["outbound"]["options"][0]["booking_link"] is None
    first, mixed, unknown = flights["outbound"]["options"]
    assert first["airline_website"] == "https://www.goindigo.in/"
    assert mixed["airline_website"] is None
    assert [leg["airline_website"] for leg in mixed["segments"]] == [
        "https://www.airindia.com/", "https://www.airindiaexpress.com/",
    ]
    assert unknown["airline_website"] is None
    hotels = client.post("/api/hotels", json={
        "destination": "Hyderabad", "check_in_date": start.isoformat(),
        "check_out_date": (start + timedelta(days=1)).isoformat(),
    }).json()
    assert hotels["hotels"][0]["link"] is None
    assert hotels["hotels"][0]["property_website"] is None
    assert hotels["hotels"][1]["property_website"] == "https://hotel.example/"
    assert hotels["hotels"][0]["search_link"].startswith("https://")
    restaurants = client.post("/api/restaurants", json={"destination": "Hyderabad"}).json()
    assert restaurants["restaurants"][0]["maps_link"].startswith("https://")
    agent_factory.assert_not_called()


def test_flight_search_resolves_supported_city_and_reports_unknown_city(
    client, tools, http_post, response, monkeypatch,
):
    monkeypatch.setattr("main.get_tools", lambda: tools)
    http_post.return_value = response([
        {"price": 2500, "currency": "INR", "segments": [
            {"departureAirport": "COK", "arrivalAirport": "VTZ"},
        ]},
    ])
    trip = {
        "source": "Kochi", "destination": "Visakhapatnam", "trip_type": "oneway",
        "start_date": (date.today() + timedelta(days=1)).isoformat(), "travellers": 1,
    }
    result = client.post("/api/flights", json=trip)
    assert result.status_code == 200
    assert result.json()["outbound"]["options"][0]["price"] == 2500
    payload = http_post.call_args.kwargs["json"]
    assert (payload["origins"], payload["destinations"]) == (["COK"], ["VTZ"])
    result = client.post("/api/flights", json={**trip, "destination": "Unknown Test City"})
    assert result.status_code == 422
    assert "Could not identify an airport" in result.json()["detail"]
    http_post.assert_called_once()

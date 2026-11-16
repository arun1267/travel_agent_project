from unittest.mock import Mock

import pytest
import requests

from tools import DestinationNotFound, SearchUnavailable


@pytest.mark.parametrize(("place", "expected"), [
    (" hyd ", "HYD"),
    ("New Delhi", "DEL"),
    ("Goa", "GOI"),
    ("Kochi", "COK"),
    ("Visakhapatnam", "VTZ"),
])
def test_resolve_airport_code(tools, place, expected):
    assert tools.resolve_airport_code(place) == expected


def test_unknown_airport(tools):
    with pytest.raises(ValueError, match="Could not identify an airport"):
        tools.resolve_airport_code("No Airport Exists In This Test City")


def flight(price, origin="DEL", destination="BOM"):
    return {
        "price": price,
        "currency": "INR",
        "totalDurationMinutes": 90,
        "segments": [{
            "airline": "Test Air",
            "flightNumber": "TA 100",
            "departureAirport": origin,
            "arrivalAirport": destination,
            "departureDateTime": "2030-01-10T09:00",
            "arrivalDateTime": "2030-01-10T10:30",
        }],
    }


def test_flight_parsing(tools, http_post, response):
    foreign_stop = flight(3000)
    foreign_stop["segments"] = flight(3000, "DEL", "DXB")["segments"] + flight(3000, "DXB", "BOM")["segments"]
    http_post.return_value = response([flight(5500), foreign_stop, flight(4000)])
    result = tools.search_flights("DEL", "BOM", "2030-01-10")
    first, second = result["options"]
    assert (first["price"], second["price"]) == (4000, 5500)
    assert first["total_duration_minutes"] == 90
    segment = first["segments"][0]
    assert (segment["from"], segment["to"], segment["flight_number"]) == ("DEL", "BOM", "TA 100")
    assert segment["arrival_time"] == "2030-01-10T10:30"
    assert http_post.call_args.kwargs["headers"] == {"Authorization": "Bearer test-apify"}


def test_round_trip_limits_and_price_order(tools, http_post, response):
    outbound = [flight(price) for price in [900, 300, 700, 100, 800, 600]]
    returns = [flight(price, "BOM", "DEL") for price in [90, 30, 70, 10, 80, 60]]
    http_post.side_effect = [response(outbound), response(returns)]
    result = tools.search_flights("DEL", "BOM", "2030-01-10", "2030-01-13", adults=2)
    assert [row["price"] for row in result["options"]] == [100, 300, 600, 700, 800]
    assert [row["price"] for row in result["shared_return_options"]] == [10, 30, 60, 70, 80]
    assert result["options"][0]["estimated_two_way_price"] == 110
    assert tools.calculate_budget(result, None, travellers=2)["flight"]["total"] == 110
    inbound_payload = http_post.call_args.kwargs["json"]
    assert inbound_payload["origins"] == ["BOM"]
    assert inbound_payload["destinations"] == ["DEL"]
    assert inbound_payload["departureDate"] == "2030-01-13"


def test_hotel_parsing_and_sorting_use_total_stay_price(tools, http_get, response):
    http_get.return_value = response({"properties": [
        {"name": "Low nightly price", "rate_per_night": {"extracted_lowest": 1000},
         "total_rate": {"extracted_lowest": 5000}},
        {"name": "Unknown total", "total_rate": {"extracted_lowest": "unknown"}},
        {"name": "Low total", "rate_per_night": {"extracted_lowest": 1500, "lowest": "₹1,500"},
         "total_rate": {"extracted_lowest": 3000}, "overall_rating": 4.5,
         "link": "https://example.test/hotel"},
    ]})
    hotels = tools.search_hotels("Goa", "2030-01-10", "2030-01-12")["hotels"]
    assert [row["total_price"] for row in hotels] == [3000, 5000, None]
    assert hotels[0]["nightly_price"] == 1500
    assert hotels[0]["nightly_price_display"] == "₹1,500"
    assert hotels[0]["overall_rating"] == 4.5
    assert hotels[0]["link"] == "https://example.test/hotel"


@pytest.mark.parametrize("budget, within, remaining", [(20000, True, 6600), (10000, False, -3400)])
def test_budget_uses_complete_round_trip_fare(tools, budget, within, remaining):
    result = tools.calculate_budget(
        {"trip_type": "round_trip", "options": [{"price": 1000}, {"round_trip_price_from": 2500}]},
        {"hotels": [{"total_price": 5000}, {"total_price": 3400}]},
        travellers=2, nights=2, total_budget=budget,
    )
    assert result["flight"]["total"] == 5000
    assert result["hotel"]["total"] == 3400
    assert result["total_estimated"] == 13400
    assert result["within_budget"] is within
    assert result["remaining_budget"] == remaining


def test_budget_with_missing_hotel_cost_is_a_subtotal(tools):
    result = tools.calculate_budget({"options": [{"price": 1000}]}, None, total_budget=5000)
    assert result["total_estimated"] == 2700
    assert result["missing_costs"] == ["hotels"]
    assert result["within_budget"] is None
    assert result["remaining_budget"] is None


def test_round_trip_requires_return_date(tools, http_post):
    with pytest.raises(ValueError, match="return_date is required"):
        tools.search_flights("DEL", "BOM", "2030-01-10", trip_type="round_trip")
    http_post.assert_not_called()


@pytest.mark.parametrize("departure", ["not-a-date", "2030-02-30"])
def test_provider_rejected_dates_are_reported_without_retry(tools, http_post, response, departure):
    http_post.return_value = response({"error": "Invalid date"}, status=400)
    with pytest.raises(SearchUnavailable):
        tools.search_flights("DEL", "BOM", departure)
    http_post.assert_called_once()


@pytest.mark.parametrize("adults", [0, -3])
def test_nonpositive_flight_travellers_are_clamped(tools, http_post, response, adults):
    http_post.return_value = response([])
    result = tools.search_flights("DEL", "BOM", "2030-01-10", adults=adults)
    assert http_post.call_args.kwargs["json"]["adults"] == 1
    assert result["travellers"] == 1


def test_nonnumeric_traveller_count_is_rejected(tools, http_post):
    with pytest.raises(ValueError):
        tools.search_flights("DEL", "BOM", "2030-01-10", adults="many")
    http_post.assert_not_called()


def test_dispatch_calls_only_supported_tools(tools, monkeypatch):
    search = Mock(return_value={"hotels": []})
    monkeypatch.setattr(tools, "search_hotels", search)
    args = {"destination": "Goa", "check_in_date": "2030-01-10", "check_out_date": "2030-01-12"}
    assert tools.run_tool("search_hotels", args) == {"hotels": []}
    search.assert_called_once_with(**args)
    with pytest.raises(ValueError, match="Unknown tool"):
        tools.run_tool("calculate_budget", {})


@pytest.mark.parametrize("args", [{}, {"unexpected": True}])
def test_dispatch_rejects_invalid_arguments(tools, http_get, args):
    with pytest.raises(TypeError):
        tools.run_tool("search_flights", args)
    http_get.assert_not_called()


def test_transient_http_errors_recover_with_backoff(tools, http_get, response, monkeypatch):
    sleep = Mock()
    monkeypatch.setattr("tools.time.sleep", sleep)
    http_get.side_effect = [requests.ReadTimeout(), response({}, 503), response({})]
    assert tools.search_hotels("Goa", "2030-01-10", "2030-01-12")["hotels"] == []
    assert http_get.call_count == 3
    assert [call.args for call in sleep.call_args_list] == [(0.5,), (1.0,)]
    assert http_get.call_args.kwargs["timeout"] == (3, 8)


def test_timeout_exhaustion_and_permanent_error(tools, http_get, response, monkeypatch):
    sleep = Mock()
    monkeypatch.setattr("tools.time.sleep", sleep)
    http_get.side_effect = requests.ConnectTimeout("private URL")
    with pytest.raises(SearchUnavailable, match="temporarily unavailable") as error:
        tools.search_hotels("Goa", "2030-01-10", "2030-01-12")
    assert "private URL" not in str(error.value)
    assert http_get.call_count == 3
    assert sleep.call_count == 2

    http_get.reset_mock(side_effect=True)
    sleep.reset_mock()
    http_get.return_value = response({}, 401)
    with pytest.raises(SearchUnavailable):
        tools.search_hotels("Goa", "2030-01-10", "2030-01-12")
    http_get.assert_called_once()
    sleep.assert_not_called()


def test_empty_flight_and_place_searches(tools, http_get, http_post, response):
    http_post.return_value = response([])
    http_get.side_effect = [
        response({"results": [{"lat": 15.5, "lon": 73.8}]}),
        response({"features": []}),
        response({"results": []}),
    ]
    assert tools.search_flights("DEL", "BOM", "2030-01-10")["options"] == []
    assert tools.search_restaurants("Goa")["restaurants"] == []
    with pytest.raises(DestinationNotFound):
        tools.search_attractions("Goa")
    assert http_get.call_count == 3

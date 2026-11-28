"""Public flight response with outbound and return legs in separate sections."""

from typing import Any
from urllib.parse import urlencode

from tools import airline_website, public_link


def flight_view(result: dict[str, Any]) -> dict[str, Any]:
    source = result["source"]
    destination = result["destination"]
    separate = result["search_mode"] == "separate_one_way_searches"
    round_trip = result["trip_type"] == "round_trip"

    def section(origin, target, travel_date, options, fare_type):
        return {
            "source": origin, "destination": target, "date": travel_date,
            "fare_type": fare_type, "options": options,
        }

    def option_view(option, origin, target, travel_date):
        view = {key: value for key, value in option.items() if key != "return_options"}
        view["booking_link"] = public_link(option.get("booking_link"))
        if "segments" in view:
            view["segments"] = [
                {**segment, "airline_website": airline_website(segment.get("airline"))}
                for segment in view["segments"] or []
            ]
        websites = {segment["airline_website"] for segment in view.get("segments", [])}
        view["airline_website"] = next(iter(websites)) if len(websites) == 1 else None
        # Use a public search link when there is no provider checkout URL.
        route_query = f"Flights from {origin} to {target} on {travel_date}"
        if round_trip and not separate:
            route_query = (
                f"Round trip flights from {source} to {destination} departing "
                f"{result['departure_date']} returning {result['return_date']}"
            )
        metadata = result.get("search_metadata") or {}
        google_url = metadata.get("google_flights_url")
        provider_search = (
            public_link(google_url)
            if isinstance(google_url, str) and google_url.startswith("https://www.google.com/travel/flights")
            else None
        )
        view["search_link"] = (
            provider_search if not separate else None
        ) or "https://www.google.com/travel/flights?" + urlencode({"q": route_query})
        return view

    outbound = []
    returns = []
    for index, option in enumerate(result["options"], 1):
        view = option_view(option, source, destination, result["departure_date"])
        outbound.append({"id": index, **view, "price": view.get("round_trip_price_from", view.get("price"))})
        if round_trip and not separate:
            for row in option.get("return_options", []):
                view = option_view(row, destination, source, result["return_date"])
                returns.append({"outbound_id": index, **view})
    if separate:
        returns = [
            option_view(row, destination, source, result["return_date"])
            for row in result.get("shared_return_options", [])
        ]
    # Sort across all compatible pairings while preserving each outbound reference.
    returns.sort(key=lambda row: row["price"] if isinstance(row.get("price"), (int, float)) else float("inf"))
    fare_type = "round_trip_total" if round_trip and not separate else "one_way"
    return {
        "source": source,
        "destination": destination,
        "trip_type": result["trip_type"],
        "search_mode": result["search_mode"],
        "currency": result["currency"],
        "outbound": section(source, destination, result["departure_date"], outbound, fare_type),
        "return": section(destination, source, result["return_date"], returns, fare_type) if round_trip else None,
        "warnings": result.get("warnings", []),
    }

"""Travel search tools (SerpApi flights/hotels + Geoapify places/restaurants)."""

from __future__ import annotations

import os
from typing import Any

import airportsdata
import requests

from log import log_step


class TravelTools:
    """Connects the travel agent to SerpApi and Geoapify."""

    SERPAPI_URL = "https://serpapi.com/search.json"
    GEOAPIFY_GEOCODE_URL = "https://api.geoapify.com/v1/geocode/search"
    GEOAPIFY_PLACES_URL = "https://api.geoapify.com/v2/places"

    # Tourism / leisure categories that work well for "places to visit"
    ATTRACTION_CATEGORIES = (
        "tourism.sights,"
        "tourism.attraction,"
        "tourism.attraction.viewpoint,"
        "leisure.park,"
        "entertainment.museum,"
        "heritage"
    )

    # Small airports Google Flights often cannot round-trip on its own.
    NEARBY_AIRPORTS = {
        "AGR": "DEL",
        "GWL": "DEL",
        "KNU": "LKO",
        "DBR": "PAT",
        "IXU": "PNQ",
        "SLV": "IXC",
        "DHM": "IXC",
    }

    def __init__(
        self,
        serpapi_key: str | None = None,
        geoapify_key: str | None = None,
        timeout: int = 45,
    ):
        self.serpapi_key = serpapi_key or os.getenv("SERPAPI_KEY")
        self.geoapify_key = geoapify_key or os.getenv("GEOAPIFY_KEY")
        self.timeout = timeout
        self.airports = airportsdata.load("IATA")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        response = requests.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(data["error"])
        return data

    def _require_key(self, key: str | None, name: str) -> None:
        if not key:
            raise RuntimeError(f"{name} is missing. Add it to your .env file.")

    def resolve_airport_code(self, place: str) -> str:
        """Resolve a city/airport name to an IATA code."""
        value = place.strip()
        if len(value) == 3 and value.isalpha():
            return value.upper()

        needle = value.casefold()
        matches: list[tuple[str, dict]] = []
        for code, airport in self.airports.items():
            city = str(airport.get("city", "")).casefold()
            name = str(airport.get("name", "")).casefold()
            if needle == city or needle == name:
                matches.append((code, airport))

        if not matches:
            for code, airport in self.airports.items():
                city = str(airport.get("city", "")).casefold()
                name = str(airport.get("name", "")).casefold()
                if needle in city or needle in name:
                    matches.append((code, airport))

        if not matches:
            raise ValueError(
                f"Could not identify an airport for '{place}'. "
                "Please provide the 3-letter IATA airport code (for example, HYD)."
            )

        # Prefer international airports when multiple matches exist.
        matches.sort(
            key=lambda item: (
                0 if "international" in item[1].get("name", "").lower() else 1,
                item[0],
            )
        )
        return matches[0][0]

    def _geocode(self, destination: str) -> tuple[float, float, str, dict]:
        """Return lat, lon, formatted address, and full first result."""
        self._require_key(self.geoapify_key, "GEOAPIFY_KEY")
        data = self._get(
            self.GEOAPIFY_GEOCODE_URL,
            {
                "text": destination,
                "format": "json",
                "limit": 1,
                "apiKey": self.geoapify_key,
            },
        )
        results = data.get("results", [])
        if not results:
            raise ValueError(f"Could not find map coordinates for '{destination}'.")
        result = results[0]
        return (
            float(result["lat"]),
            float(result["lon"]),
            result.get("formatted", destination),
            result,
        )

    # ------------------------------------------------------------------
    # Public tools
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_trip_type(trip_type: str | None, return_date: str | None) -> str:
        value = (trip_type or "").lower().replace("-", "_").replace(" ", "_")
        if value in {"one_way", "oneway", "single"}:
            return "one_way"
        if value in {"round_trip", "roundtrip", "two_way", "twoway", "return"}:
            return "round_trip"
        return "round_trip" if return_date else "one_way"

    @staticmethod
    def _summarize_flight(item: dict[str, Any]) -> dict[str, Any]:
        segments = []
        for segment in item.get("flights", []):
            dep = segment.get("departure_airport", {})
            arr = segment.get("arrival_airport", {})
            segments.append(
                {
                    "airline": segment.get("airline"),
                    "flight_number": segment.get("flight_number"),
                    "from": dep.get("name") or dep.get("id"),
                    "departure_time": dep.get("time"),
                    "to": arr.get("name") or arr.get("id"),
                    "arrival_time": arr.get("time"),
                    "duration_minutes": segment.get("duration"),
                }
            )
        return {
            "price": item.get("price"),
            "total_duration_minutes": item.get("total_duration"),
            "stops": item.get("layovers", []),
            "segments": segments,
            "booking_link": item.get("link"),
        }

    def _flight_results(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        return list(data.get("best_flights") or []) + list(
            data.get("other_flights") or []
        )

    def _search_google_flights(
        self, params: dict[str, Any]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        response = requests.get(
            self.SERPAPI_URL, params=params, timeout=self.timeout
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            return {}, []
        results = self._flight_results(data)
        error = data.get("error")
        if error and not results:
            message = str(error).lower()
            if "hasn't returned any results" in message:
                return data, []
            raise RuntimeError(error)
        return data, results

    def _attach_return_flights(
        self,
        outbound_results: list[dict[str, Any]],
        base_params: dict[str, Any],
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        flights: list[dict[str, Any]] = []
        for item in outbound_results[:limit]:
            option = self._summarize_flight(item)
            option["return_options"] = []
            token = item.get("departure_token")
            if not token:
                option["return_search_error"] = (
                    "This outbound option did not include a departure_token."
                )
                flights.append(option)
                continue
            return_params = dict(base_params)
            return_params["departure_token"] = token
            try:
                _, return_results = self._search_google_flights(return_params)
                option["return_options"] = [
                    self._summarize_flight(ret) for ret in return_results[:4]
                ]
                priced = [
                    ret["price"]
                    for ret in option["return_options"]
                    if isinstance(ret.get("price"), (int, float))
                ]
                if priced:
                    option["round_trip_price_from"] = min(priced)
                log_step(
                    "return_flights",
                    f"outbound={option['segments'][0].get('departure_time') if option['segments'] else '?'} "
                    f"returns={len(option['return_options'])}",
                )
            except Exception as return_error:
                option["return_search_error"] = str(return_error)
            flights.append(option)
        return flights

    def _one_way_params(
        self,
        origin: str,
        destination: str,
        date: str,
        adults: int,
        currency: str,
    ) -> dict[str, Any]:
        return {
            "engine": "google_flights",
            "type": "2",
            "departure_id": origin,
            "arrival_id": destination,
            "outbound_date": date,
            "adults": adults,
            "currency": currency,
            "hl": "en",
            "gl": "in",
            "api_key": self.serpapi_key,
        }

    def search_flights(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        return_date: str | None = None,
        adults: int = 1,
        currency: str = "INR",
        trip_type: str | None = None,
    ) -> dict[str, Any]:
        """Search Google Flights via SerpApi (indicative only)."""
        self._require_key(self.serpapi_key, "SERPAPI_KEY")
        departure_id = self.resolve_airport_code(origin)
        arrival_id = self.resolve_airport_code(destination)
        normalized_trip = self._normalize_trip_type(trip_type, return_date)
        is_round_trip = normalized_trip == "round_trip"
        adults = max(1, int(adults))
        warnings: list[str] = []

        if is_round_trip and not return_date:
            raise ValueError("return_date is required for a two-way / round trip.")

        params: dict[str, Any] = {
            "engine": "google_flights",
            "type": "1" if is_round_trip else "2",
            "departure_id": departure_id,
            "arrival_id": arrival_id,
            "outbound_date": departure_date,
            "adults": adults,
            "currency": currency,
            "hl": "en",
            "gl": "in",
            "api_key": self.serpapi_key,
        }
        if is_round_trip:
            params["return_date"] = return_date

        log_step(
            "search_flights",
            f"{departure_id}->{arrival_id} type={normalized_trip} "
            f"{departure_date}" + (f"/{return_date}" if is_round_trip else ""),
        )
        data, results = self._search_google_flights(params)
        search_mode = "round_trip" if is_round_trip else "one_way"

        if is_round_trip and not results:
            nearby_dep = self.NEARBY_AIRPORTS.get(departure_id)
            nearby_arr = self.NEARBY_AIRPORTS.get(arrival_id)
            if nearby_dep or nearby_arr:
                nearby_params = dict(params)
                if nearby_dep:
                    nearby_params["departure_id"] = f"{departure_id},{nearby_dep}"
                if nearby_arr:
                    nearby_params["arrival_id"] = f"{arrival_id},{nearby_arr}"
                log_step("search_flights", "retry round trip with nearby airports")
                nearby_data, nearby_results = self._search_google_flights(nearby_params)
                if nearby_results:
                    data, results, params = nearby_data, nearby_results, nearby_params
                    search_mode = "round_trip_nearby_airports"
                    warnings.append(
                        "No direct round-trip results for the exact airports. "
                        "Results include nearby airports "
                        f"({nearby_params['departure_id']} → {nearby_params['arrival_id']})."
                    )

        if is_round_trip and not results:
            log_step("search_flights", "fallback to separate one-way searches")
            out_data, out_results = self._search_google_flights(
                self._one_way_params(
                    departure_id, arrival_id, departure_date, adults, currency
                )
            )
            ret_origin, ret_dest = arrival_id, departure_id
            _, ret_results = self._search_google_flights(
                self._one_way_params(
                    ret_origin, ret_dest, return_date, adults, currency
                )
            )
            if not ret_results:
                nearby_home = self.NEARBY_AIRPORTS.get(departure_id)
                if nearby_home:
                    _, ret_results = self._search_google_flights(
                        self._one_way_params(
                            arrival_id,
                            f"{departure_id},{nearby_home}",
                            return_date,
                            adults,
                            currency,
                        )
                    )
                    if ret_results:
                        warnings.append(
                            f"Return one-way results include nearby airport {nearby_home}."
                        )
            if out_results:
                data = out_data
                search_mode = "separate_one_way_searches"
                warnings.append(
                    "Google Flights had no packaged round-trip for this route. "
                    "Outbound and return are separate one-way searches; add the "
                    "two prices for an estimate."
                )
                return_options = [
                    self._summarize_flight(item) for item in ret_results[:4]
                ]
                flights = []
                for item in out_results[:4]:
                    option = self._summarize_flight(item)
                    option["return_options"] = return_options
                    out_price = option.get("price")
                    ret_prices = [
                        row["price"]
                        for row in return_options
                        if isinstance(row.get("price"), (int, float))
                    ]
                    if isinstance(out_price, (int, float)) and ret_prices:
                        option["estimated_two_way_price"] = out_price + min(ret_prices)
                    flights.append(option)
                return {
                    "route": f"{departure_id} to {arrival_id}",
                    "trip_type": normalized_trip,
                    "search_mode": search_mode,
                    "departure_date": departure_date,
                    "return_date": return_date,
                    "travellers": adults,
                    "currency": currency,
                    "options": flights,
                    "warnings": warnings,
                    "note": (
                        "Indicative search results only. No ticket has been booked. "
                        "Show outbound AND return_options in the user-facing plan."
                    ),
                    "search_metadata": data.get("search_metadata", {}),
                }

        if is_round_trip:
            flights = self._attach_return_flights(results, params)
        else:
            flights = [self._summarize_flight(item) for item in results[:6]]

        flights.sort(
            key=lambda row: row.get("round_trip_price_from")
            or row.get("estimated_two_way_price")
            or row.get("price")
            or 10**12
        )

        return {
            "route": f"{departure_id} to {arrival_id}",
            "trip_type": normalized_trip,
            "search_mode": search_mode,
            "departure_date": departure_date,
            "return_date": return_date if is_round_trip else None,
            "travellers": adults,
            "currency": currency,
            "options": flights,
            "warnings": warnings,
            "note": (
                "Indicative search results only. No ticket has been booked. "
                "For two-way trips, each option includes return_options with times. "
                "round_trip_price_from is the full round-trip fare when present."
            ),
            "search_metadata": data.get("search_metadata", {}),
        }

    def search_hotels(
        self,
        destination: str,
        check_in_date: str,
        check_out_date: str,
        adults: int = 1,
        rooms: int = 1,
        currency: str = "INR",
    ) -> dict[str, Any]:
        """Search hotel listings via SerpApi Google Hotels."""
        self._require_key(self.serpapi_key, "SERPAPI_KEY")
        params = {
            "engine": "google_hotels",
            "q": destination,
            "check_in_date": check_in_date,
            "check_out_date": check_out_date,
            "adults": max(1, int(adults)),
            "rooms": max(1, int(rooms)),
            "currency": currency,
            "gl": "in",
            "hl": "en",
            "api_key": self.serpapi_key,
        }
        data = self._get(self.SERPAPI_URL, params)
        properties = data.get("properties", [])
        hotels = []
        for hotel in properties[:12]:
            rate = hotel.get("rate_per_night", {})
            total = hotel.get("total_rate", {})
            hotels.append(
                {
                    "name": hotel.get("name"),
                    "overall_rating": hotel.get("overall_rating"),
                    "reviews": hotel.get("reviews"),
                    "nightly_price": rate.get("lowest") or rate.get("extracted_lowest"),
                    "total_price": total.get("lowest") or total.get("extracted_lowest"),
                    "currency": currency,
                    "link": hotel.get("link"),
                    "type": hotel.get("type"),
                    "nearby_places": hotel.get("nearby_places", []),
                }
            )
        hotels.sort(
            key=lambda row: row["total_price"]
            if isinstance(row.get("total_price"), (int, float))
            else 10**12
        )
        return {
            "destination": destination,
            "check_in": check_in_date,
            "check_out": check_out_date,
            "travellers": adults,
            "rooms": rooms,
            "hotels": hotels,
            "note": "Indicative listings only. No room has been reserved.",
        }

    def search_restaurants(
        self,
        destination: str,
        radius_meters: int = 5000,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Find restaurants near the destination using Geoapify Places."""
        lat, lon, formatted, _ = self._geocode(destination)
        data = self._get(
            self.GEOAPIFY_PLACES_URL,
            {
                "categories": "catering.restaurant",
                "filter": f"circle:{lon},{lat},{max(500, int(radius_meters))}",
                "bias": f"proximity:{lon},{lat}",
                "limit": min(max(int(limit), 1), 20),
                "apiKey": self.geoapify_key,
            },
        )
        restaurants = []
        for feature in data.get("features", []):
            prop = feature.get("properties", {})
            restaurants.append(
                {
                    "name": prop.get("name"),
                    "address": prop.get("formatted") or prop.get("address_line2"),
                    "cuisine_or_categories": prop.get("categories", []),
                    "website": prop.get("website"),
                    "opening_hours": prop.get("opening_hours"),
                    "distance_meters": prop.get("distance"),
                    "latitude": prop.get("lat"),
                    "longitude": prop.get("lon"),
                }
            )
        return {
            "destination": formatted,
            "restaurants": restaurants,
            "note": "Restaurant search results; verify current opening hours before visiting.",
        }

    def search_attractions(
        self,
        destination: str,
        radius_meters: int = 12000,
        limit: int = 12,
    ) -> dict[str, Any]:
        """
        Find sights, viewpoints, parks and other places worth visiting
        near the destination. Also returns a short place snapshot from geocoding.
        """
        lat, lon, formatted, geo = self._geocode(destination)
        data = self._get(
            self.GEOAPIFY_PLACES_URL,
            {
                "categories": self.ATTRACTION_CATEGORIES,
                "filter": f"circle:{lon},{lat},{max(1000, int(radius_meters))}",
                "bias": f"proximity:{lon},{lat}",
                "limit": min(max(int(limit), 1), 20),
                "apiKey": self.geoapify_key,
            },
        )

        places = []
        for feature in data.get("features", []):
            prop = feature.get("properties", {})
            places.append(
                {
                    "name": prop.get("name"),
                    "address": prop.get("formatted") or prop.get("address_line2"),
                    "categories": prop.get("categories", []),
                    "website": prop.get("website"),
                    "opening_hours": prop.get("opening_hours"),
                    "distance_meters": prop.get("distance"),
                    "latitude": prop.get("lat"),
                    "longitude": prop.get("lon"),
                }
            )

        # Lightweight place snapshot (no invented text)
        snapshot = {
            "formatted_address": formatted,
            "city": geo.get("city") or geo.get("county"),
            "state": geo.get("state"),
            "country": geo.get("country"),
            "latitude": lat,
            "longitude": lon,
            "timezone": (
                geo.get("timezone", {}).get("name")
                if isinstance(geo.get("timezone"), dict)
                else None
            ),
        }

        return {
            "destination": formatted,
            "place_snapshot": snapshot,
            "attractions": places,
            "note": (
                "Attraction results from map data. Names and presence only; "
                "entry fees, opening hours and suitability should be verified locally."
            ),
        }

    # ------------------------------------------------------------------
    # Bedrock tool specs + dispatcher
    # ------------------------------------------------------------------

    @staticmethod
    def bedrock_tool_specs() -> list[dict[str, Any]]:
        """Tool schemas expected by the Bedrock Converse API."""
        return [
            {
                "toolSpec": {
                    "name": "search_flights",
                    "description": (
                        "Search flights. Use trip_type=round_trip with return_date "
                        "for two-way travel (includes return_options with times). "
                        "Use trip_type=one_way with no return_date for one-way. "
                        "Dates use YYYY-MM-DD. Prefer cheaper options when a budget "
                        "is given."
                    ),
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "origin": {"type": "string"},
                                "destination": {"type": "string"},
                                "departure_date": {"type": "string"},
                                "return_date": {"type": "string"},
                                "trip_type": {
                                    "type": "string",
                                    "enum": ["round_trip", "one_way"],
                                },
                                "adults": {"type": "integer", "minimum": 1},
                                "currency": {"type": "string"},
                            },
                            "required": [
                                "origin",
                                "destination",
                                "departure_date",
                                "adults",
                            ],
                        }
                    },
                }
            },
            {
                "toolSpec": {
                    "name": "search_hotels",
                    "description": "Search hotel options for the destination and stay dates.",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "destination": {"type": "string"},
                                "check_in_date": {"type": "string"},
                                "check_out_date": {"type": "string"},
                                "adults": {"type": "integer", "minimum": 1},
                                "rooms": {"type": "integer", "minimum": 1},
                                "currency": {"type": "string"},
                            },
                            "required": [
                                "destination",
                                "check_in_date",
                                "check_out_date",
                                "adults",
                            ],
                        }
                    },
                }
            },
            {
                "toolSpec": {
                    "name": "search_restaurants",
                    "description": "Find restaurants around the destination.",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "destination": {"type": "string"},
                                "radius_meters": {"type": "integer"},
                                "limit": {"type": "integer"},
                            },
                            "required": ["destination"],
                        }
                    },
                }
            },
            {
                "toolSpec": {
                    "name": "search_attractions",
                    "description": (
                        "Get a short place snapshot and a list of sights, "
                        "viewpoints, parks and attractions near the destination. "
                        "Always call this for the destination so the plan can "
                        "recommend real places to visit."
                    ),
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "destination": {"type": "string"},
                                "radius_meters": {"type": "integer"},
                                "limit": {"type": "integer"},
                            },
                            "required": ["destination"],
                        }
                    },
                }
            },
        ]

    def run_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run only the tools exposed to the model."""
        allowed = {
            "search_flights": self.search_flights,
            "search_hotels": self.search_hotels,
            "search_restaurants": self.search_restaurants,
            "search_attractions": self.search_attractions,
        }
        function = allowed.get(tool_name)
        if function is None:
            raise ValueError(f"Unknown tool requested by the model: {tool_name}")
        return function(**arguments)
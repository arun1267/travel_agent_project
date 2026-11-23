"""Travel search tools (Apify flights, SerpApi hotels + Geoapify places/restaurants)."""

from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import urlencode, urlsplit

import airportsdata
import requests

from log import log_event, track_operation


AIRLINE_WEBSITES = {
    "indigo": "https://www.goindigo.in/",
    "airindia": "https://www.airindia.com/",
    "airindiaexpress": "https://www.airindiaexpress.com/",
    "akasaair": "https://www.akasaair.com/",
    "spicejet": "https://www.spicejet.com/",
    "allianceair": "https://www.allianceair.in/",
    "starair": "https://starair.co.in/",
    "fly91": "https://fly91.in/",
}


def airline_website(airline: Any) -> str | None:
    if not isinstance(airline, str):
        return None
    key = "".join(character for character in airline.casefold() if character.isalnum())
    return AIRLINE_WEBSITES.get(key)


def public_link(value: Any) -> str | None:
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return None
    try:
        url = urlsplit(value)
        host = url.hostname
    except ValueError:
        return None
    if url.scheme != "https" or not host or url.username is not None:
        return None
    if host == "serpapi.com" or host.endswith(".serpapi.com"):
        return None
    return value


class SearchUnavailable(RuntimeError):
    """A provider could not complete the search."""


class FlightProviderConfigurationError(SearchUnavailable):
    """A safe flight configuration error to expose through the API."""


class DestinationNotFound(ValueError):
    """The map provider returned no matching destination."""


class FlightRouteError(ValueError):
    """The flight route could not be resolved."""


class TravelTools:
    """Connects the travel agent to Apify, SerpApi, and Geoapify."""

    APIFY_FLIGHTS_URL = (
        "https://api.apify.com/v2/actors/famed_bonobo~google-flights-scraper"
        "/run-sync-get-dataset-items"
    )
    APIFY_RUN_TIMEOUT = 75
    SERPAPI_URL = "https://serpapi.com/search.json"
    GEOAPIFY_GEOCODE_URL = "https://api.geoapify.com/v1/geocode/search"
    GEOAPIFY_PLACES_URL = "https://api.geoapify.com/v2/places"
    ROUND_TRIP_OPTIONS_PER_LEG = 5
    CITY_AIRPORTS = {
        "goa": "GOI",
        "kochi": "COK",
        "visakhapatnam": "VTZ",
        "vizag": "VTZ",
    }

    ATTRACTION_CATEGORIES = (
        "tourism.sights,"
        "tourism.attraction,"
        "tourism.attraction.viewpoint,"
        "leisure.park,"
        "entertainment.museum,"
        "heritage"
    )

    def __init__(
        self,
        serpapi_key: str | None = None,
        geoapify_key: str | None = None,
        timeout: int = 8,
        apify_token: str | None = None,
    ):
        self.apify_token = (apify_token or os.getenv("APIFY_TOKEN") or "").strip()
        self.serpapi_key = serpapi_key or os.getenv("SERPAPI_KEY")
        self.geoapify_key = geoapify_key or os.getenv("GEOAPIFY_KEY")
        self.timeout = timeout
        self.airports = airportsdata.load("IATA")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        if url == self.SERPAPI_URL:
            provider = "SerpApi"
            operation = "hotels"
        else:
            provider = "Geoapify"
            operation = "geocode" if url == self.GEOAPIFY_GEOCODE_URL else "places"
        for attempt in range(3):
            try:
                with track_operation("api_call", provider, operation=operation, attempt=attempt + 1) as details:
                    response = requests.get(url, params=params, timeout=(3, self.timeout))
                    if isinstance(response.status_code, int):
                        details["http_status"] = response.status_code
                        if response.status_code >= 400:
                            details["error_code"] = f"http_{response.status_code}"
                    response.raise_for_status()
                    return self._parse_response(response, provider, details)
            except requests.exceptions.SSLError:
                raise SearchUnavailable(f"{provider} connection could not be verified.") from None
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as error:
                status = getattr(error.response, "status_code", None)
                retryable = (
                    isinstance(error, (requests.Timeout, requests.ConnectionError))
                    or status in {408, 429, 500, 502, 503, 504}
                )
                if not retryable or attempt == 2:
                    raise SearchUnavailable(f"{provider} search is temporarily unavailable.") from None
                time.sleep(0.5 * 2**attempt)
                continue
            except requests.RequestException:
                raise SearchUnavailable(f"{provider} search is temporarily unavailable.") from None

    @staticmethod
    def _parse_response(response, provider: str, details: dict) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            details["error_code"] = "invalid_response"
            raise SearchUnavailable(f"{provider} returned an invalid response.")
        error = data.get("error")
        if error:
            details["error_code"] = "provider_error"
            raise SearchUnavailable(f"{provider} search is temporarily unavailable.")
        return data

    def _require_key(self, key: str | None, name: str) -> None:
        if not key:
            log_event("search_configuration", setting=name, error_code="missing_credential")
            raise SearchUnavailable("Search is temporarily unavailable.")

    def resolve_airport_code(self, place: str) -> str:
        """Resolve a city/airport name to an IATA code."""
        value = place.strip()
        needle = value.casefold()
        if needle in self.CITY_AIRPORTS:
            return self.CITY_AIRPORTS[needle]
        if value.upper() in self.airports:
            return value.upper()
        if not needle or len(value) == 3:
            raise FlightRouteError("Airport code is unknown. Enter a city or a valid IATA airport code.")
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
            raise FlightRouteError(
                "Could not identify an airport for the requested city. "
                "Please provide the 3-letter IATA airport code (for example, HYD)."
            )

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
        results = data.get("results") or []
        if not results:
            raise DestinationNotFound("The map search found no matching destination.")
        result = results[0]
        return (
            float(result["lat"]),
            float(result["lon"]),
            result.get("formatted", destination),
            result,
        )

    @staticmethod
    def _safe_number(value: Any) -> float | None:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        return None

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

    def _is_indian_airport(self, code: str) -> bool:
        return self.airports.get(code, {}).get("country") == "IN"

    def _search_apify_flights(
        self, origin: str, destination: str, date: str, adults: int, currency: str,
    ) -> list[dict[str, Any]]:
        payload = {
            "origins": [origin],
            "destinations": [destination],
            "departureDate": date,
            "adults": adults,
            "country": "in",
            "currency": currency,
            "maxFlightsPerSearch": 15,
        }
        with track_operation("api_call", "Apify", operation="flights", attempt=1) as details:
            try:
                response = requests.post(
                    self.APIFY_FLIGHTS_URL,
                    headers={"Authorization": f"Bearer {self.apify_token}"},
                    params={"timeout": self.APIFY_RUN_TIMEOUT, "maxTotalChargeUsd": 0.10},
                    json=payload,
                    timeout=(3, self.APIFY_RUN_TIMEOUT + 10),
                )
                details["http_status"] = response.status_code
                response.raise_for_status()
                rows = response.json()
            except (requests.RequestException, ValueError) as error:
                status = details.get("http_status")
                details["error_code"] = f"http_{status}" if status and status >= 400 else type(error).__name__
                if status == 401:
                    raise FlightProviderConfigurationError(
                        "Apify rejected the flight API token. Check APIFY_TOKEN in the backend environment."
                    ) from None
                if status == 403:
                    raise FlightProviderConfigurationError(
                        "Apify denied access to the flight actor. Check its permissions and account usage."
                    ) from None
                raise SearchUnavailable("Apify flight search is temporarily unavailable.") from None
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                details["error_code"] = "invalid_response"
                raise SearchUnavailable("Apify returned an invalid flight response.")

        options = []
        for row in rows:
            legs = row.get("segments")
            if not isinstance(legs, list) or not legs or not all(isinstance(leg, dict) for leg in legs):
                continue
            if row.get("currency") != currency or not all(
                isinstance(code, str) and self._is_indian_airport(code)
                for leg in legs for code in (leg.get("departureAirport"), leg.get("arrivalAirport"))
            ):
                continue
            if legs[0]["departureAirport"] != origin or legs[-1]["arrivalAirport"] != destination:
                continue
            segments = [
                {
                    "airline": leg.get("airline"),
                    "airline_website": airline_website(leg.get("airline")),
                    "flight_number": leg.get("flightNumber"),
                    "from": leg["departureAirport"],
                    "to": leg["arrivalAirport"],
                    "departure_time": leg.get("departureDateTime"),
                    "arrival_time": leg.get("arrivalDateTime"),
                    "duration_minutes": leg.get("durationMinutes"),
                }
                for leg in legs
            ]
            options.append({
                "price": self._safe_number(row.get("price")),
                "airline_logo": None,
                "total_duration_minutes": row.get("totalDurationMinutes"),
                "stops": [
                    {"id": stop.get("airport"), "name": stop.get("airportName") or stop.get("airport"),
                     "duration": stop.get("durationMinutes")}
                    for stop in row.get("layovers") or [] if isinstance(stop, dict)
                ],
                "segments": segments,
                "booking_link": None,
            })
        return sorted(options, key=lambda row: row["price"] if row["price"] is not None else float("inf"))

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
        """Search indicative fares for domestic Indian flights through Apify."""
        departure_id = self.resolve_airport_code(origin)
        arrival_id = self.resolve_airport_code(destination)
        if departure_id == arrival_id:
            raise FlightRouteError("Origin and destination resolve to the same airport.")
        if not all(self._is_indian_airport(code) for code in (departure_id, arrival_id)):
            raise FlightRouteError("Flight search supports domestic routes within India.")

        normalized_trip = self._normalize_trip_type(trip_type, return_date)
        is_round_trip = normalized_trip == "round_trip"
        if is_round_trip and not return_date:
            raise ValueError("return_date is required for a two-way / round trip.")
        adults = max(1, int(adults))
        currency = currency.upper()
        if not self.apify_token:
            log_event("search_configuration", setting="APIFY_TOKEN", error_code="missing_credential")
            raise FlightProviderConfigurationError(
                "Flight search requires APIFY_TOKEN in the backend environment."
            )

        outbound = self._search_apify_flights(departure_id, arrival_id, departure_date, adults, currency)
        warnings = []
        return_options = []
        if not outbound:
            warnings.append("No outbound flight options were found.")
        if return_date and is_round_trip:
            outbound = outbound[:self.ROUND_TRIP_OPTIONS_PER_LEG]
            warnings.append("Outbound and return fares are separate one-way searches. Add the two fares for an estimate.")
            try:
                return_options = self._search_apify_flights(arrival_id, departure_id, return_date, adults, currency)
                return_options = return_options[:self.ROUND_TRIP_OPTIONS_PER_LEG]
                if not return_options:
                    warnings.append("No return flight options were found.")
            except SearchUnavailable:
                warnings.append("Return flight information is temporarily unavailable.")
            cheapest_return = next((row["price"] for row in return_options if row["price"] is not None), None)
            if cheapest_return is not None:
                for option in outbound:
                    if option["price"] is not None:
                        option["estimated_two_way_price"] = option["price"] + cheapest_return
        else:
            outbound = outbound[:6]

        return {
            "route": f"{departure_id} to {arrival_id}",
            "source": origin,
            "destination": destination,
            "trip_type": normalized_trip,
            "search_mode": "separate_one_way_searches" if is_round_trip else "one_way",
            "departure_date": departure_date,
            "return_date": return_date if is_round_trip else None,
            "travellers": adults,
            "prices_include_all_travellers": True,
            "currency": currency,
            "options": outbound,
            "shared_return_options": return_options,
            "warnings": warnings,
            "note": "Indicative fares. Round-trip estimates use two separately priced one-way flights.",
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
        properties = data.get("properties") or []
        hotels = []
        for hotel in properties[:12]:
            rate = hotel.get("rate_per_night", {}) or {}
            total = hotel.get("total_rate", {}) or {}

            nightly_numeric = rate.get("extracted_lowest")
            total_numeric = total.get("extracted_lowest")
            image_url = next(
                (url for image in hotel.get("images") or [] if isinstance(image, dict)
                 if (url := public_link(image.get("thumbnail")) or public_link(image.get("original_image")))),
                None,
            )

            if not isinstance(nightly_numeric, (int, float)):
                nightly_numeric = None
            if not isinstance(total_numeric, (int, float)):
                total_numeric = None

            hotels.append(
                {
                    "name": hotel.get("name"),
                    "image_url": image_url,
                    "overall_rating": hotel.get("overall_rating"),
                    "reviews": hotel.get("reviews"),
                    "nightly_price": nightly_numeric,
                    "total_price": total_numeric,
                    "nightly_price_display": rate.get("lowest"),
                    "total_price_display": total.get("lowest"),
                    "currency": currency,
                    "link": public_link(hotel.get("link")),
                    "property_website": public_link(hotel.get("website")) or public_link(hotel.get("link")),
                    "search_link": "https://www.google.com/travel/hotels?" + urlencode({
                        "q": f"{hotel.get('name') or 'Hotels'} {destination}",
                    }),
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
        for feature in data.get("features") or []:
            prop = feature.get("properties", {})
            restaurants.append(
                {
                    "name": prop.get("name"),
                    "image_url": public_link(prop.get("image")),
                    "address": prop.get("formatted") or prop.get("address_line2"),
                    "cuisine_or_categories": prop.get("categories", []),
                    "website": public_link(prop.get("website")),
                    "maps_link": "https://www.google.com/maps/search/?" + urlencode({
                        "api": 1, "query": f"{prop.get('name') or 'Restaurants'} {formatted}",
                    }),
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
        """Find sights, viewpoints, parks and other places worth visiting."""
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
        for feature in data.get("features") or []:
            prop = feature.get("properties", {})
            name = prop.get("name")
            if not name:
                continue
            address = prop.get("formatted") or prop.get("address_line2")
            places.append(
                {
                    "name": name,
                    "address": address,
                    "categories": prop.get("categories", []),
                    "website": public_link(prop.get("website")),
                    "maps_link": "https://www.google.com/maps/search/?" + urlencode({
                        "api": 1, "query": f"{name} {address or formatted}",
                    }),
                    "opening_hours": prop.get("opening_hours"),
                    "distance_meters": prop.get("distance"),
                    "latitude": prop.get("lat"),
                    "longitude": prop.get("lon"),
                }
            )

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
    # Budget calculation (Python does the math)
    # ------------------------------------------------------------------

    def calculate_budget(
        self,
        flight_result: dict[str, Any] | None,
        hotel_result: dict[str, Any] | None,
        travellers: int | None = 1,
        nights: int | None = 1,
        rooms: int | None = 1,
        total_budget: float | None = None,
        food_per_person_per_day: float = 800.0,
        local_transport_per_day: float = 600.0,
        other_per_day: float = 300.0,
    ) -> dict[str, Any]:
        """
        Pure Python budget calculation.
        - Apify fares cover all travellers. Legacy fares are treated as per adult.
        - Hotel total_price is treated as the full stay cost.
        """
        travellers = max(1, int(travellers)) if travellers is not None else None
        nights = max(0, int(nights)) if nights is not None else None
        rooms = max(1, int(rooms)) if rooms is not None else None

        notes: list[str] = []
        warnings: list[str] = []

        # ---------- Flights ----------
        flight_cost = None
        flight_per_person = None
        selected_flight_label = None

        if flight_result and isinstance(flight_result, dict):
            options = flight_result.get("options") or []
            if options:
                best = None
                best_price = None
                for opt in options:
                    price = (
                        self._safe_number(opt.get("round_trip_price_from"))
                        or self._safe_number(opt.get("estimated_two_way_price"))
                        or (
                            self._safe_number(opt.get("price"))
                            if flight_result.get("trip_type") != "round_trip"
                            else None
                        )
                    )
                    if price is not None and (best_price is None or price < best_price):
                        best_price = price
                        best = opt

                if best_price is not None:
                    if flight_result.get("prices_include_all_travellers"):
                        flight_cost = best_price
                        fare_travellers = flight_result.get("travellers") or travellers
                        if fare_travellers:
                            flight_per_person = best_price / fare_travellers
                        notes.append("Apify fares cover all travellers in the flight search.")
                    else:
                        flight_per_person = best_price
                        if travellers is not None:
                            flight_cost = best_price * travellers
                            notes.append(f"Per-adult flight fare multiplied by {travellers} travellers.")
                        else:
                            warnings.append("Traveller count is required to total the per-adult flight fare.")
                    if flight_per_person is not None:
                        selected_flight_label = (
                            f"Cheapest available option (per adult ≈ ₹{flight_per_person:,.0f})"
                        )
                else:
                    warnings.append("No usable numeric flight price found.")
            else:
                warnings.append("No flight options available for budget calculation.")
        else:
            warnings.append("Flight search result missing.")

        # ---------- Hotels ----------
        hotel_cost = None
        selected_hotel_name = None

        if hotel_result and isinstance(hotel_result, dict):
            hotels = hotel_result.get("hotels") or []
            if hotels:
                best_hotel = None
                best_total = None
                for h in hotels:
                    total = self._safe_number(h.get("total_price"))
                    if total is not None and (best_total is None or total < best_total):
                        best_total = total
                        best_hotel = h

                if best_total is not None:
                    hotel_cost = best_total
                    selected_hotel_name = best_hotel.get("name") or "Selected hotel"
                    notes.append("Hotel total_price covers the rooms and dates in the hotel search.")
                else:
                    warnings.append("No usable numeric hotel total_price found.")
            else:
                warnings.append("No hotel options available for budget calculation.")
        else:
            warnings.append("Hotel search result missing.")

        # ---------- Daily estimates ----------
        food_cost = (
            food_per_person_per_day * travellers * nights
            if travellers is not None and nights is not None else None
        )
        local_transport_cost = local_transport_per_day * nights if nights is not None else None
        other_cost = other_per_day * nights if nights is not None else None
        if nights is None:
            warnings.append("Daily expenses require a known stay duration.")
        elif travellers is None:
            warnings.append("Food costs require a known traveller count.")

        notes.append(
            f"Food estimated at ₹{food_per_person_per_day:,.0f} per person per day."
        )
        notes.append(
            f"Local transport estimated at ₹{local_transport_per_day:,.0f} per day."
        )
        notes.append(
            f"Other / miscellaneous estimated at ₹{other_per_day:,.0f} per day."
        )

        # ---------- Totals ----------
        parts = [flight_cost, hotel_cost, food_cost, local_transport_cost, other_cost]
        known_parts = [p for p in parts if p is not None]
        total_estimated = sum(known_parts) if known_parts else None

        missing_costs = []
        if flight_cost is None:
            missing_costs.append("flights")
        if hotel_cost is None:
            missing_costs.append("hotels")
        for label, cost in (("food", food_cost), ("local transport", local_transport_cost), ("other", other_cost)):
            if cost is None:
                missing_costs.append(label)
        remaining = None
        within_budget = None
        if total_budget is not None and total_estimated is not None:
            if total_estimated > total_budget:
                within_budget = False
            if not missing_costs:
                remaining = total_budget - total_estimated
                within_budget = remaining >= 0

        return {
            "travellers": travellers,
            "nights": nights,
            "rooms": rooms,
            "currency": "INR",
            "flight": {
                "per_adult": flight_per_person,
                "total": flight_cost,
                "label": selected_flight_label,
            },
            "hotel": {
                "total": hotel_cost,
                "name": selected_hotel_name,
            },
            "food": {
                "per_person_per_day": food_per_person_per_day,
                "total": food_cost,
            },
            "local_transport": {
                "per_day": local_transport_per_day,
                "total": local_transport_cost,
            },
            "other": {
                "per_day": other_per_day,
                "total": other_cost,
            },
            "total_estimated": total_estimated,
            "missing_costs": missing_costs,
            "stated_budget": total_budget,
            "remaining_budget": remaining,
            "within_budget": within_budget,
            "notes": notes,
            "warnings": warnings,
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

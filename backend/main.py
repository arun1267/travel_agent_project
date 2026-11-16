"""HTTP entry point for the travel planner."""

from datetime import date
from functools import lru_cache
import os
from pathlib import Path
from typing import Annotated, Any, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints, model_validator

from agent import TravelAgent
from flight_view import flight_view
from log import log_event, track_request
from tools import (
    DestinationNotFound,
    FlightProviderConfigurationError,
    FlightRouteError,
    SearchUnavailable,
    TravelTools,
)


load_dotenv(Path(__file__).with_name(".env"))
app = FastAPI(title="TravelBuddy API")

allowed_origins = os.getenv("CORS_ORIGINS") or "http://localhost:5173,http://127.0.0.1:5173"
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in allowed_origins.split(",") if origin.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

City = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class TripDetails(BaseModel):
    source: City
    destination: City
    trip_type: Literal["round", "oneway"]
    travellers: int = Field(strict=True, ge=1, le=9)
    budget: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    start_date: date
    end_date: date | None = None

    @model_validator(mode="after")
    def validate_trip(self):
        if self.source.casefold() == self.destination.casefold():
            raise ValueError("Origin and destination must be different.")
        if self.start_date < date.today():
            raise ValueError("Departure date must be today or later.")
        if self.trip_type == "round":
            if self.end_date is None or self.end_date < self.start_date:
                raise ValueError("A round trip requires a return date on or after departure.")
        elif self.end_date is not None:
            raise ValueError("A one-way trip cannot include a return date.")
        return self

    def context(self) -> str:
        trip_type = "round trip" if self.trip_type == "round" else "one-way trip"
        context = (
            f"{trip_type} from {self.source} to {self.destination} for "
            f"{self.travellers} travellers, departing {self.start_date.isoformat()}"
        )
        if self.end_date is not None:
            context += f", returning {self.end_date.isoformat()}"
        if self.budget is not None:
            context += f", budget ₹{self.budget:,.2f}"
        return context


class ChatRequest(BaseModel):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    session_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
    trip: TripDetails | None = None


class ChatResponse(BaseModel):
    answer: str
    results: dict[str, dict[str, Any] | None]
    notices: list[str]
    budget: dict[str, Any] | None
    outcome: Literal["success", "partial", "error"]


@lru_cache(maxsize=1)
def get_agent() -> TravelAgent:
    return TravelAgent(tools=get_tools())


@lru_cache(maxsize=1)
def get_tools() -> TravelTools:
    return TravelTools()


class HotelSearch(BaseModel):
    destination: City
    check_in_date: date
    check_out_date: date
    travellers: int = Field(default=1, strict=True, ge=1, le=9)
    rooms: int = Field(default=1, strict=True, ge=1, le=9)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.check_in_date < date.today() or self.check_out_date <= self.check_in_date:
            raise ValueError("Check-in must be today or later, and check-out must follow check-in.")
        return self


class RestaurantSearch(BaseModel):
    destination: City
    radius_meters: int = Field(default=5000, ge=500, le=50000)
    limit: int = Field(default=10, ge=1, le=20)


def run_search(method, **kwargs):
    with track_request() as metrics:
        try:
            result = getattr(get_tools(), method)(**kwargs)
        except DestinationNotFound:
            metrics.outcome = "empty"
            return {"destination": kwargs["destination"], "restaurants": [],
                    "notice": "The map search found no matching destination."}
        except FlightProviderConfigurationError as error:
            raise HTTPException(503, str(error)) from error
        except SearchUnavailable as error:
            raise HTTPException(503, "The search provider is temporarily unavailable.") from error
        except FlightRouteError as error:
            log_event("search_rejected", operation=method, error_code="invalid_flight_route")
            raise HTTPException(422, str(error)) from error
        except ValueError as error:
            log_event("search_failed", operation=method, error_code="invalid_provider_response")
            raise HTTPException(502, "The search provider returned data we could not process.") from error
        metrics.outcome = "success"
        return result


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/cities")
def cities() -> list[str]:
    airports = get_tools().airports.values()
    return sorted({
        airport["city"].strip()
        for airport in airports
        if airport.get("country") == "IN" and (airport.get("city") or "").strip()
    }, key=str.casefold)


@app.post("/api/flights")
def flights(request: TripDetails):
    result = run_search(
        "search_flights", origin=request.source, destination=request.destination,
        departure_date=request.start_date.isoformat(),
        return_date=request.end_date.isoformat() if request.end_date else None,
        trip_type="round_trip" if request.trip_type == "round" else "one_way",
        adults=request.travellers,
    )
    return flight_view(result)


@app.post("/api/hotels")
def hotels(request: HotelSearch):
    return run_search(
        "search_hotels", destination=request.destination,
        check_in_date=request.check_in_date.isoformat(),
        check_out_date=request.check_out_date.isoformat(), adults=request.travellers,
        rooms=request.rooms,
    )


@app.post("/api/restaurants")
def restaurants(request: RestaurantSearch):
    return run_search("search_restaurants", **request.model_dump())


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    # Validate the request before constructing clients or invoking providers.
    try:
        agent = get_agent()
    except Exception as error:
        log_event("agent_initialization_failed", error_type=type(error).__name__)
        return JSONResponse(status_code=503, content={"detail": "The travel planner is temporarily unavailable."})
    message = request.message
    if request.trip is not None:
        message = f"Trip details: {request.trip.context()}.\n\nUser request: {message}"
    response = agent.chat_with_results(message, request.session_id)
    result = response["results"].get("flights")
    if result:
        response["results"]["flights"] = flight_view(result)
    return response

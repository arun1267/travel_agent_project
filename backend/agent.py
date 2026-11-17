"""Bedrock travel agent: model invocation + tool-use loop + Redis memory + budget."""

from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    ConnectionClosedError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from redis.exceptions import RedisError

from log import log_event, record_bedrock_usage, track_operation, track_request
from memory import TravelMemory
from prompt import SYSTEM_PROMPT
from tools import (
    DestinationNotFound,
    FlightProviderConfigurationError,
    FlightRouteError,
    SearchUnavailable,
    TravelTools,
)


TOOL_RESULTS = {
    "search_flights": ("Flight", "options"),
    "search_hotels": ("Hotel", "hotels"),
    "search_restaurants": ("Restaurant", "restaurants"),
    "search_attractions": ("Attraction", "attractions"),
}
RETRYABLE_BEDROCK_CODES = {"ModelTimeoutException", "ModelNotReadyException", "ThrottlingException"}
LOGGED_BEDROCK_CODES = RETRYABLE_BEDROCK_CODES | {
    "AccessDeniedException", "ValidationException", "ResourceNotFoundException",
    "InternalServerException", "ServiceUnavailableException", "ModelErrorException",
}


def format_round_trip(result: dict[str, Any]) -> str:
    """Render the sorted search results directly so the model cannot omit rows."""
    currency = result.get("currency", "INR")
    price_prefix = "₹" if currency == "INR" else f"{currency} "
    separate = result.get("search_mode") == "separate_one_way_searches"

    def describe(option: dict[str, Any], price: Any) -> str:
        segments = option.get("segments") or []
        first, last = (segments[0], segments[-1]) if segments else ({}, {})
        name = " ".join(
            value for value in (first.get("airline"), first.get("flight_number")) if value
        ) or "Flight"
        departure = first.get("departure_time") or "time unavailable"
        arrival = last.get("arrival_time") or "time unavailable"
        fare = f"{price_prefix}{price:,.0f}" if isinstance(price, (int, float)) else "price unavailable"
        duration = option.get("total_duration_minutes")
        duration_text = f" · {duration} min" if duration else ""
        return f"{name} · {departure} → {arrival}{duration_text} · {fare}"

    options = result.get("options") or []
    lines = [
        f"### Outbound flights · {result.get('source', '')} → {result.get('destination', '')} · {result.get('departure_date', '')}",
        "Prices are one-way fares." if separate else "Prices are round-trip fares from the amount shown.",
        "",
    ]
    for index, option in enumerate(options, 1):
        price = option.get("price") if separate else option.get("round_trip_price_from", option.get("price"))
        lines.append(f"{index}. {describe(option, price)}")
    if not options:
        lines.append("No outbound options were found.")

    lines.extend(["", f"### Return flights · {result.get('destination', '')} → {result.get('source', '')} · {result.get('return_date', '')}"])
    if separate:
        lines.extend(["Prices are one-way fares.", ""])
        returns = result.get("shared_return_options") or []
        lines.extend(f"{i}. {describe(row, row.get('price'))}" for i, row in enumerate(returns, 1))
        if not returns:
            lines.append("No return options are available from this search.")
    else:
        lines.append("Prices are for the outbound and return together.")
        for index, option in enumerate(options, 1):
            lines.extend(["", f"**With outbound option {index}**", ""])
            returns = option.get("return_options") or []
            lines.extend(f"{i}. {describe(row, row.get('price'))}" for i, row in enumerate(returns, 1))
            if not returns:
                lines.append("No compatible return options are available from this search.")
        if not options:
            lines.append("No return options are available from this search.")

    return "\n".join(lines)


class TravelAgent:
    """Runs the Bedrock model and handles its tool-use cycle."""

    def __init__(
        self,
        tools: TravelTools | None = None,
        memory: TravelMemory | None = None,
        model_id: str | None = None,
        region_name: str | None = None,
        max_tool_rounds: int = 8,
    ):
        self.tools = tools or TravelTools()
        self.memory = memory or TravelMemory()
        self.model_id = model_id or os.getenv(
            "BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0"
        )
        self.region_name = region_name or os.getenv(
            "AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        )
        self.max_tool_rounds = max_tool_rounds

        self.client = boto3.client(
            "bedrock-runtime",
            region_name=self.region_name,
            config=Config(
                connect_timeout=3,
                read_timeout=25,
                retries={"total_max_attempts": 1},
            ),
        )
        log_event("agent_ready")

    def _inference_config(self) -> dict[str, Any]:
        """GPT-6 / GPT-5.x on Bedrock reject temperature and topP."""
        config: dict[str, Any] = {"maxTokens": 3500}
        model = self.model_id.lower()
        uses_sampling = not any(
            token in model
            for token in (
                "openai.gpt-6",
                "openai.gpt-5",
            )
        )
        if uses_sampling:
            config["temperature"] = 0.2
            config["topP"] = 0.9
        return config

    def _invoke_model(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        request = dict(
            modelId=self.model_id,
            system=[{"text": SYSTEM_PROMPT}],
            messages=messages,
            toolConfig={"tools": self.tools.bedrock_tool_specs()},
            inferenceConfig=self._inference_config(),
        )
        # Keep SDK retries disabled so the same limit applies to model-side timeouts.
        for attempt in range(3):
            try:
                with track_operation("api_call", "bedrock", operation="converse", attempt=attempt + 1) as details:
                    try:
                        response = self.client.converse(**request)
                    except ClientError as error:
                        code = error.response.get("Error", {}).get("Code")
                        details["error_code"] = code if code in LOGGED_BEDROCK_CODES else "other_client_error"
                        raise
                    record_bedrock_usage(response, details)
                    return response
            except (ConnectTimeoutError, ReadTimeoutError, EndpointConnectionError, ConnectionClosedError):
                if attempt == 2:
                    raise
            except ClientError as error:
                code = error.response.get("Error", {}).get("Code")
                status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                if attempt == 2 or (
                    code not in RETRYABLE_BEDROCK_CODES
                    and status not in {408, 429, 500, 502, 503, 504}
                ):
                    raise
            time.sleep(0.5 * 2**attempt)

    def _memory_call(self, operation, *args):
        try:
            with track_operation("memory_operation", "redis", operation=operation.__name__):
                return operation(*args)
        except RedisError:
            return None

    def _build_context_note(self, session_id: str) -> str:
        """Inject a short note about previously stored structured results."""
        stored = self._memory_call(self.memory.get_all_tool_results, session_id) or {}
        parts = []
        if stored.get("flights"):
            options = stored["flights"].get("options") or []
            parts.append(f"Previous flight search has {len(options)} options stored.")
        if stored.get("hotels"):
            hotels = stored["hotels"].get("hotels") or []
            parts.append(f"Previous hotel search has {len(hotels)} hotels stored.")
        if stored.get("attractions"):
            result = stored["attractions"]
            places = [
                {field: place[field] for field in ("name", "address", "categories", "opening_hours")
                 if place.get(field)}
                for place in (result.get("attractions") or [])[:20]
                if place.get("name")
            ]
            if places:
                parts.append("Previous places to visit (map data): " + json.dumps({
                    "destination": result.get("destination"),
                    "places": places,
                }, ensure_ascii=False))
        if stored.get("restaurants"):
            parts.append("Previous restaurant results are stored.")

        if not parts:
            return ""

        return (
            "\n\n[System note – previous structured tool results are available "
            "in memory for this session. Prefer using them for follow-up questions "
            "like 'compare option 2 and 4' instead of searching again unless the "
            "user asks for a new search.]\n"
            + " ".join(parts)
        )

    def _extract_trip_params(self, user_message: str) -> dict[str, Any]:
        """Lightweight extraction of travellers, budget, nights from user text."""
        travellers = 2
        nights = 3
        rooms = 1
        total_budget = None

        msg_lower = user_message.lower()

        m_trav = re.search(
            r"(\d+)\s*(?:people|travellers|travelers|adults|persons)", msg_lower
        )
        if m_trav:
            travellers = max(1, int(m_trav.group(1)))

        m_budget = re.search(r"(?:budget|under)\s*₹?\s*([\d,]+)", msg_lower)
        if m_budget:
            try:
                total_budget = float(m_budget.group(1).replace(",", ""))
            except ValueError:
                pass

        # Try to detect nights from date patterns (very simple)
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", user_message)
        if len(dates) >= 2:
            try:
                from datetime import datetime
                d1 = datetime.strptime(dates[0], "%Y-%m-%d")
                d2 = datetime.strptime(dates[1], "%Y-%m-%d")
                delta = (d2 - d1).days
                if delta > 0:
                    nights = delta
            except Exception:
                pass

        return {
            "travellers": travellers,
            "nights": nights,
            "rooms": rooms,
            "total_budget": total_budget,
        }

    def chat(
        self,
        user_message: str,
        session_id: str,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> str:
        """Send a message, execute tools, calculate budget, return answer."""
        return self.chat_with_results(user_message, session_id, conversation_history)["answer"]

    def chat_with_results(
        self,
        user_message: str,
        session_id: str,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Return the answer and structured results from this request."""
        with track_request() as metrics:
            return self._chat(user_message, session_id, conversation_history, metrics)

    def _run_search_tool(self, name, arguments):
        _, key = TOOL_RESULTS.get(name, ("Travel", ""))
        with track_operation("tool_call", "tools", tool=name if key else "unknown") as details:
            try:
                result = self.tools.run_tool(name, arguments)
            except DestinationNotFound:
                result = {key: [], "note": "The map search found no matching destination."}
            if key and not (result.get(key) or result.get("shared_return_options")):
                details["outcome"] = "empty"
            return result

    def _chat(self, user_message, session_id, conversation_history, metrics) -> dict[str, Any]:
        if conversation_history is None:
            simple_history = self._memory_call(self.memory.get_messages, session_id) or []
            conversation_history = self.to_bedrock_history(simple_history)

        messages = list(conversation_history or [])

        context_note = self._build_context_note(session_id)
        full_user_message = user_message + context_note

        messages.append(
            {
                "role": "user",
                "content": [{"text": full_user_message}],
            }
        )

        self._memory_call(self.memory.append_message, session_id, "user", user_message)
        current_results: dict[str, dict[str, Any] | None] = {}
        notices: dict[str, str] = {}
        unavailable: dict[str, str] = {}
        searched: dict[tuple[str, str], tuple[dict[str, Any], str | None]] = {}
        budget_data = None

        def finish(answer: str, outcome: str = "success") -> dict[str, Any]:
            flight_result = current_results.get("search_flights") or {}
            if flight_result.get("trip_type") == "round_trip" and (
                flight_result.get("options") or flight_result.get("shared_return_options")
            ):
                answer += "\n\n" + format_round_trip(flight_result)
            if notices:
                answer += "\n\n**Search updates**\n" + "\n".join(f"- {notice}" for notice in notices.values())
            self._memory_call(self.memory.append_message, session_id, "assistant", answer)
            metrics.outcome = "partial" if outcome == "success" and notices else outcome
            return {
                "answer": answer,
                "results": {
                    name.removeprefix("search_"): result
                    for name, result in current_results.items()
                },
                "notices": list(notices.values()),
                "budget": budget_data,
                "outcome": metrics.outcome,
            }

        def partial_answer(reason: str) -> dict[str, Any]:
            lines = [reason]
            for tool_name, result in current_results.items():
                if not result:
                    continue
                label, key = TOOL_RESULTS[tool_name]
                rows = result.get(key) or []
                if key == "options" and result.get("trip_type") == "round_trip":
                    continue
                if key == "options" and rows:
                    lines.append(f"The flight search found {len(rows)} outbound options.")
                    for option in rows[:3]:
                        segments = option.get("segments") or []
                        if segments:
                            first, last = segments[0], segments[-1]
                            lines.append(
                                f"- {first.get('airline') or 'Flight'} from "
                                f"{first.get('departure_time') or 'time unavailable'} to "
                                f"{last.get('arrival_time') or 'time unavailable'}."
                            )
                elif names := [row["name"] for row in rows[:3] if row.get("name")]:
                    lines.append(f"{label} options found: {', '.join(names)}.")
            has_results = any(result and (
                result.get(TOOL_RESULTS[name][1]) or result.get("shared_return_options")
            ) for name, result in current_results.items())
            return finish("\n\n".join(lines), "partial" if has_results else "error")

        try:
            for _ in range(self.max_tool_rounds):
                response = self._invoke_model(messages)
                assistant_message = response["output"]["message"]
                messages.append(assistant_message)
                stop_reason = response.get("stopReason")

                if stop_reason != "tool_use":
                    answer_parts = [
                        block["text"]
                        for block in assistant_message.get("content", [])
                        if "text" in block
                    ]
                    answer = "\n".join(answer_parts).strip()
                    if not answer:
                        return partial_answer("I couldn't generate a text response. Please try again.")
                    return finish(answer)

                tool_results = []

                calls = []
                for block in assistant_message.get("content", []):
                    if tool_use := block.get("toolUse"):
                        name = tool_use["name"]
                        arguments = tool_use.get("input", {})
                        cache_key = (name, json.dumps(arguments, sort_keys=True))
                        calls.append((tool_use, arguments, cache_key))

                with ThreadPoolExecutor(max_workers=4) as executor:
                    pending = {}
                    for tool_use, arguments, cache_key in calls:
                        name = tool_use["name"]
                        if name in unavailable or cache_key in searched or cache_key in pending:
                            continue
                        pending[cache_key] = executor.submit(
                            copy_context().run, self._run_search_tool, name, arguments
                        )

                    for tool_use, _, cache_key in calls:
                        tool_name = tool_use["name"]
                        label, key = TOOL_RESULTS.get(tool_name, ("Travel", ""))
                        logged_tool = tool_name if key else "unknown"
                        result, error = {}, None
                        if cache_key in searched:
                            result, error = searched[cache_key]
                            log_event("tool_reused", tool=logged_tool, outcome="error" if error else "success")
                        elif cache_key in pending:
                            try:
                                result = pending[cache_key].result()
                            except FlightProviderConfigurationError as provider_error:
                                error = str(provider_error)
                                unavailable[tool_name] = error
                            except SearchUnavailable:
                                error = f"{label} information is temporarily unavailable."
                                unavailable[tool_name] = error
                            except FlightRouteError as route_error:
                                error = str(route_error)
                            except Exception:
                                error = f"The {label.lower()} search could not be completed."
                            else:
                                self._memory_call(self.memory.save_tool_result, session_id, tool_name, result)
                            searched[cache_key] = (result, error)
                        else:
                            error = unavailable[tool_name]
                            log_event("tool_skipped", tool=logged_tool, reason="unavailable")

                        if key:
                            if not error or tool_name not in current_results:
                                current_results[tool_name] = result if not error else None
                            if error:
                                notices[tool_name] = error
                            elif not result.get(key):
                                notices[tool_name] = f"No {label.lower()} options were found for this search."
                                if result.get("note") == "The map search found no matching destination.":
                                    notices[tool_name] = f"{label} search found no matching destination in the map data."
                            else:
                                notices.pop(tool_name, None)
                                if key == "options" and (
                                    any(row.get("return_search_error") for row in result[key])
                                    or any(
                                        warning.startswith(("Return flight information", "No return flight"))
                                        for warning in result.get("warnings", [])
                                    )
                                ):
                                    notices[tool_name] = "Some return flight information is unavailable. Outbound options are still available."

                        tool_results.append({
                            "toolResult": {
                                "toolUseId": tool_use["toolUseId"],
                                "content": [{"text": error}] if error else [{"json": result}],
                                "status": "error" if error else "success",
                            }
                        })

                # ----- Python budget calculation -----
                flight_result = current_results.get("search_flights")
                hotel_result = current_results.get("search_hotels")
                if tool_results and (flight_result or hotel_result):
                    budget_data = self.tools.calculate_budget(
                        flight_result=flight_result,
                        hotel_result=hotel_result,
                        **self._extract_trip_params(user_message),
                    )
                    tool_results.append({"text": "Budget calculation: " + json.dumps(budget_data)})

                if not tool_results:
                    return partial_answer(
                        "The model requested a tool but did not provide a "
                        "valid tool call."
                    )

                messages.append(
                    {
                        "role": "user",
                        "content": tool_results,
                    }
                )

            log_event("request_issue", error_code="tool_round_limit")
            return partial_answer("I reached the search limit before I could finish your itinerary.")

        except (ClientError, BotoCoreError) as error:
            log_event("request_issue", error_type=type(error).__name__, service="bedrock")
            return partial_answer(
                "The itinerary service is temporarily unavailable. Please try again in a moment."
            )
        except Exception as error:
            log_event("request_issue", error_type=type(error).__name__)
            return partial_answer("I couldn't finish planning this trip. Please try again.")

    @staticmethod
    def to_bedrock_history(
        chat_messages: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """Convert simple role/content history to Converse messages."""
        history: list[dict[str, Any]] = []
        for item in chat_messages:
            role = item.get("role")
            content = item.get("content", "")
            if role in ("user", "assistant") and content:
                history.append({"role": role, "content": [{"text": content}]})
        return history

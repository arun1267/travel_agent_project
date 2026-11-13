"""Bedrock travel agent: model invocation + tool-use loop + Redis memory + budget."""

from __future__ import annotations

import os
import re
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from log import log_step
from memory import TravelMemory
from prompt import SYSTEM_PROMPT
from tools import TravelTools


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
        )
        log_step(
            "agent_ready",
            f"model={self.model_id} region={self.region_name}",
        )

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
        return self.client.converse(
            modelId=self.model_id,
            system=[{"text": SYSTEM_PROMPT}],
            messages=messages,
            toolConfig={"tools": self.tools.bedrock_tool_specs()},
            inferenceConfig=self._inference_config(),
        )

    def _build_context_note(self, session_id: str) -> str:
        """Inject a short note about previously stored structured results."""
        stored = self.memory.get_all_tool_results(session_id)
        parts = []
        if stored.get("flights"):
            options = stored["flights"].get("options") or []
            parts.append(f"Previous flight search has {len(options)} options stored.")
        if stored.get("hotels"):
            hotels = stored["hotels"].get("hotels") or []
            parts.append(f"Previous hotel search has {len(hotels)} hotels stored.")
        if stored.get("attractions"):
            parts.append("Previous attractions results are stored.")
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
        if conversation_history is None:
            simple_history = self.memory.get_messages(session_id)
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

        self.memory.append_message(session_id, "user", user_message)

        log_step("chat_start", f"session={session_id} | {user_message[:180]}")
        try:
            for round_index in range(self.max_tool_rounds):
                log_step("bedrock_call", f"round={round_index + 1}")
                response = self._invoke_model(messages)
                assistant_message = response["output"]["message"]
                messages.append(assistant_message)
                stop_reason = response.get("stopReason")
                log_step("bedrock_response", f"stopReason={stop_reason}")

                if stop_reason != "tool_use":
                    answer_parts = [
                        block["text"]
                        for block in assistant_message.get("content", [])
                        if "text" in block
                    ]
                    answer = "\n".join(answer_parts).strip() or (
                        "I couldn't generate a text response. Please try again."
                    )
                    self.memory.append_message(session_id, "assistant", answer)
                    log_step("chat_done", f"chars={len(answer)}")
                    return answer

                tool_results = []
                flight_result = None
                hotel_result = None

                for block in assistant_message.get("content", []):
                    tool_use = block.get("toolUse")
                    if not tool_use:
                        continue

                    tool_name = tool_use["name"]
                    tool_input = tool_use.get("input", {})
                    log_step("tool_call", tool_name)
                    try:
                        result = self.tools.run_tool(tool_name, tool_input)
                        self.memory.save_tool_result(session_id, tool_name, result)

                        if tool_name == "search_flights":
                            flight_result = result
                        elif tool_name == "search_hotels":
                            hotel_result = result

                        log_step("tool_ok", f"{tool_name} (saved to memory)")
                        tool_results.append(
                            {
                                "toolResult": {
                                    "toolUseId": tool_use["toolUseId"],
                                    "content": [{"json": result}],
                                    "status": "success",
                                }
                            }
                        )
                    except Exception as tool_error:
                        log_step("tool_error", f"{tool_name}: {tool_error}")
                        tool_results.append(
                            {
                                "toolResult": {
                                    "toolUseId": tool_use["toolUseId"],
                                    "content": [{"text": str(tool_error)}],
                                    "status": "error",
                                }
                            }
                        )

                # ----- Python budget calculation -----
                params = self._extract_trip_params(user_message)

                if flight_result is None:
                    flight_result = self.memory.get_tool_result(
                        session_id, "search_flights"
                    )
                if hotel_result is None:
                    hotel_result = self.memory.get_tool_result(
                        session_id, "search_hotels"
                    )

                if flight_result or hotel_result:
                    budget_data = self.tools.calculate_budget(
                        flight_result=flight_result,
                        hotel_result=hotel_result,
                        travellers=params["travellers"],
                        nights=params["nights"],
                        rooms=params["rooms"],
                        total_budget=params["total_budget"],
                    )
                    tool_results.append(
                        {
                            "toolResult": {
                                "toolUseId": "budget-calc-internal",
                                "content": [
                                    {"json": {"budget_calculation": budget_data}}
                                ],
                                "status": "success",
                            }
                        }
                    )
                    log_step(
                        "budget_calc",
                        f"total={budget_data.get('total_estimated')} "
                        f"within={budget_data.get('within_budget')}",
                    )

                if not tool_results:
                    return (
                        "The model requested a tool but did not provide a "
                        "valid tool call."
                    )

                messages.append(
                    {
                        "role": "user",
                        "content": tool_results,
                    }
                )

            log_step("chat_limit", "max tool rounds reached")
            return (
                "I reached the maximum number of tool calls for this request. "
                "Please narrow the request or try again."
            )

        except (ClientError, BotoCoreError) as error:
            log_step("bedrock_error", str(error))
            return (
                "I couldn't reach Amazon Bedrock. Check your AWS credentials, "
                f"region, model access, and IAM permissions. Details: {error}"
            )
        except Exception as error:
            log_step("chat_error", str(error))
            return f"Something went wrong while planning the trip: {error}"

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
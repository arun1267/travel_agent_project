"""Bedrock travel agent: model invocation + tool-use loop."""

from __future__ import annotations

import os
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from log import log_step
from prompt import SYSTEM_PROMPT
from tools import TravelTools


class TravelAgent:
    """Runs the Bedrock model and handles its tool-use cycle."""

    def __init__(
        self,
        tools: TravelTools | None = None,
        model_id: str | None = None,
        region_name: str | None = None,
        max_tool_rounds: int = 8,
    ):
        self.tools = tools or TravelTools()
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

    def chat(
        self,
        user_message: str,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> str:
        """Send a message, execute requested tools, and return the final answer."""
        messages = list(conversation_history or [])
        messages.append(
            {
                "role": "user",
                "content": [{"text": user_message}],
            }
        )

        log_step("chat_start", user_message[:200])
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
                    log_step("chat_done", f"chars={len(answer)}")
                    return answer

                tool_results = []
                for block in assistant_message.get("content", []):
                    tool_use = block.get("toolUse")
                    if not tool_use:
                        continue

                    tool_name = tool_use["name"]
                    tool_input = tool_use.get("input", {})
                    log_step("tool_call", tool_name)
                    try:
                        result = self.tools.run_tool(tool_name, tool_input)
                        log_step("tool_ok", tool_name)
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
        """Convert Streamlit's simple role/content history to Converse messages."""
        history: list[dict[str, Any]] = []
        for item in chat_messages:
            role = item.get("role")
            content = item.get("content", "")
            if role in ("user", "assistant") and content:
                history.append({"role": role, "content": [{"text": content}]})
        return history
"""
title: MWS Usage Stats
author: MWS GPT
version: 1.0.0
description: Tool to check model usage statistics and spending from LiteLLM.
"""

import requests
from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        LITELLM_URL: str = Field(
            default="http://litellm:4000",
            description="LiteLLM proxy base URL",
        )
        LITELLM_API_KEY: str = Field(
            default="",
            description="LiteLLM master key for API access",
        )

    def __init__(self):
        self.valves = self.Valves()

    def get_usage_stats(
        self,
        __user__: dict = {},
    ) -> str:
        """
        Get model usage statistics including spend per model and total cost.
        Use this when the user asks about usage, costs, spending, or statistics.
        """
        headers = {}
        if self.valves.LITELLM_API_KEY:
            headers["Authorization"] = f"Bearer {self.valves.LITELLM_API_KEY}"

        try:
            # Get per-model spend
            resp = requests.get(
                f"{self.valves.LITELLM_URL}/global/spend/models",
                headers=headers,
                timeout=5,
            )
            if resp.status_code != 200:
                return f"Error fetching stats: {resp.status_code}"

            models = resp.json()
            if not models:
                return "No usage data available yet."

            # Get global spend
            global_resp = requests.get(
                f"{self.valves.LITELLM_URL}/global/spend",
                headers=headers,
                timeout=5,
            )
            global_data = global_resp.json() if global_resp.status_code == 200 else {}

            lines = ["**Model Usage Statistics:**\n"]
            lines.append(f"| Model | Total Spend |")
            lines.append(f"|-------|------------|")

            for m in sorted(models, key=lambda x: x.get("total_spend", 0), reverse=True):
                model_name = m.get("model", "unknown")
                spend = m.get("total_spend", 0)
                if spend > 0:
                    lines.append(f"| {model_name} | ${spend:.4f} |")
                else:
                    lines.append(f"| {model_name} | $0.00 (free) |")

            total = global_data.get("spend", 0)
            lines.append(f"\n**Total spend:** ${total:.4f}")

            return "\n".join(lines)

        except Exception as e:
            return f"Error connecting to LiteLLM: {e}"

    def get_recent_requests(
        self,
        limit: int = 10,
        __user__: dict = {},
    ) -> str:
        """
        Get recent API request logs with model, tokens, and timing info.
        Use this when the user asks about recent activity or request history.

        :param limit: Number of recent requests to show (default 10, max 50).
        """
        if limit > 50:
            limit = 50

        headers = {}
        if self.valves.LITELLM_API_KEY:
            headers["Authorization"] = f"Bearer {self.valves.LITELLM_API_KEY}"

        try:
            resp = requests.get(
                f"{self.valves.LITELLM_URL}/spend/logs?limit={limit}",
                headers=headers,
                timeout=5,
            )
            if resp.status_code != 200:
                return f"Error fetching logs: {resp.status_code}"

            logs = resp.json()
            if not logs:
                return "No request logs available."

            lines = ["**Recent Requests:**\n"]
            for log in logs[:limit]:
                model = log.get("model", "?")
                tokens = log.get("total_tokens", 0)
                spend = log.get("spend", 0)
                duration = log.get("request_duration_ms", 0)
                start = log.get("startTime", "")[:19]

                if tokens > 0:
                    lines.append(
                        f"- [{start}] **{model}** — {tokens} tokens, "
                        f"${spend:.4f}, {duration}ms"
                    )

            if len(lines) == 1:
                return "No completed requests in logs."

            return "\n".join(lines)

        except Exception as e:
            return f"Error connecting to LiteLLM: {e}"

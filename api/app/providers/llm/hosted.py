"""Hosted free-tier provider (Groq, Google AI Studio, OpenRouter) (section 3.5)."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from pydantic import ValidationError

from ...config import Settings
from ...schemas import Answer, Passage
from .ollama import FALLBACK_ANSWER, _build_augmented_prompt

log = logging.getLogger("rahbar.provider.hosted")


class HostedProvider:
    name = "hosted"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def generate(self, question: str, passages: list[Passage]) -> Answer:
        if not self.settings.hosted_base_url or not self.settings.llm_api_key:
            log.warning("Hosted provider invoked without HOSTED_BASE_URL or LLM_API_KEY; using fallback")
            return FALLBACK_ANSWER

        prompt = _build_augmented_prompt(question, passages)
        url = f"{self.settings.hosted_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.settings.temperature,
            "response_format": {"type": "json_object"},  # Constrained decoding
        }

        # Attempt call with timeout (section 3.4)
        for attempt in range(1, 3):  # 1 initial try + 1 retry on validation failure
            try:
                with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    if resp.status_code != 200:
                        log.warning("Hosted provider returned status %d: %s", resp.status_code, resp.text)
                        continue

                    data = resp.json()
                    choices = data.get("choices", [])
                    if not choices:
                        continue
                    raw_content = choices[0].get("message", {}).get("content", "")

                    # Validate output strictly with Pydantic
                    parsed_answer = Answer.model_validate_json(raw_content)
                    return parsed_answer

            except (ValidationError, json.JSONDecodeError) as val_err:
                log.warning("Attempt %d failed Pydantic validation on hosted provider: %s", attempt, val_err)
                if attempt == 1:
                    payload["messages"].append({"role": "assistant", "content": raw_content if "raw_content" in locals() else "{}"})
                    payload["messages"].append({
                        "role": "user",
                        "content": "Your previous response was not valid JSON matching the schema. Please return only valid JSON.",
                    })
            except Exception as exc:
                log.error("Hosted provider connection exception on attempt %d: %s", attempt, exc)

        log.error("Hosted provider failed after retry; returning safe canned fallback")
        return FALLBACK_ANSWER

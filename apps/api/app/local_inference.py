"""Small local-inference seam used by Incident Prefill only.

The rest of the application has no knowledge of Ollama's HTTP protocol. A
fake adapter is intentionally provided so normal tests never download or
load a model.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Protocol

import httpx


class LocalInferenceError(RuntimeError):
    """A bounded local mapper failure; callers must preserve manual fallback."""


class LocalInferenceBusy(LocalInferenceError):
    """The bounded local queue is full."""


class LocalInference(Protocol):
    name: str
    model: str

    def complete_json(self, prompt: str) -> dict:
        """Return strict JSON or raise LocalInferenceError."""


@dataclass
class FakeLocalInference:
    """Deterministic adapter for unit/API tests."""

    response: dict
    name: str = "fake"
    model: str = "fake-local"
    delay_seconds: float = 0.0

    def complete_json(self, prompt: str) -> dict:
        del prompt
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        return self.response


class OllamaLocalInference:
    """CPU-oriented Ollama adapter with a bounded, single-flight queue."""

    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        context_size: int,
        keep_alive: str,
        timeout_seconds: float,
        max_concurrency: int = 1,
        max_queue: int = 8,
    ) -> None:
        if max_concurrency != 1:
            raise ValueError("Incident Prefill local inference must remain single-concurrency")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.context_size = context_size
        self.keep_alive = keep_alive
        self.timeout_seconds = timeout_seconds
        self._inference_slot = threading.Lock()
        self._queue_slots = threading.BoundedSemaphore(max_queue)

    def complete_json(self, prompt: str) -> dict:
        if not self._queue_slots.acquire(blocking=False):
            raise LocalInferenceBusy("local prefill queue is full")
        try:
            with self._inference_slot:
                return self._complete_with_bounded_retry(prompt)
        finally:
            self._queue_slots.release()

    def _complete_with_bounded_retry(self, prompt: str) -> dict:
        retry_prompt = prompt
        for attempt in range(2):
            try:
                response = httpx.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.model,
                        "messages": [
                            {
                                "role": "system",
                                "content": "Return one JSON object only. Never invent facts absent from the supplied OCR evidence.",
                            },
                            {"role": "user", "content": retry_prompt},
                        ],
                        "stream": False,
                        "format": "json",
                        "keep_alive": self.keep_alive,
                        "options": {
                            "num_ctx": self.context_size,
                            "num_predict": 128,
                            "temperature": 0,
                        },
                    },
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                body = response.json()
                content = (body.get("message") or {}).get("content") or body.get("response")
                if not isinstance(content, str):
                    raise ValueError("Ollama response did not contain message.content")
                result = json.loads(content)
                if not isinstance(result, dict):
                    raise ValueError("Ollama response JSON must be an object")
                return result
            except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
                if attempt == 1:
                    raise LocalInferenceError(f"Ollama local prefill failed: {exc}") from exc
                retry_prompt = f"{prompt}\nReturn strict JSON without markdown or commentary."

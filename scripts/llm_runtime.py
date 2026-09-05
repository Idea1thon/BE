"""Provider-neutral JSON LLM runtime.

The pipeline uses an OpenAI-compatible chat-completions endpoint only when
``LLM_API_URL`` and ``LLM_API_KEY`` are configured. No network call is made in
offline mode or when auto mode has no configured endpoint. Raw prompts and
responses are intentionally not logged here.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from _env import load_env
except ImportError:  # pragma: no cover - direct standalone import fallback
    load_env = None

if load_env is not None:
    load_env()


class LLMRuntimeError(RuntimeError):
    """The configured LLM endpoint could not return valid JSON."""


@dataclass(frozen=True)
class LLMConfig:
    mode: str = "auto"
    endpoint: str | None = None
    api_key: str | None = None
    model: str = ""
    timeout_s: float = 20.0
    max_output_tokens: int = 1200

    @classmethod
    def from_env(cls, mode: str = "auto") -> "LLMConfig":
        if mode not in {"auto", "required", "offline"}:
            raise ValueError(f"지원하지 않는 llm mode: {mode}")
        endpoint = os.getenv("LLM_API_URL", "").strip() or None
        api_key = os.getenv("LLM_API_KEY", "").strip() or None
        model = os.getenv("LLM_MODEL", "").strip()
        try:
            timeout_s = float(os.getenv("LLM_TIMEOUT_SECONDS", "20"))
        except ValueError:
            timeout_s = 20.0
        try:
            max_output_tokens = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "1200"))
        except ValueError:
            max_output_tokens = 1200
        return cls(mode, endpoint, api_key, model, max(1.0, timeout_s), max(128, max_output_tokens))

    @property
    def available(self) -> bool:
        return self.mode != "offline" and bool(self.endpoint and self.api_key and self.model)

    def public_metadata(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "configured": self.available,
            "model": self.model or None,
            "endpoint_configured": bool(self.endpoint),
        }


def _chat_url(endpoint: str) -> str:
    endpoint = endpoint.rstrip("/")
    return endpoint if endpoint.endswith("/chat/completions") else f"{endpoint}/chat/completions"


def _extract_json(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        return content
    if isinstance(content, list):
        content = "".join(str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in content)
    text = str(content or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise LLMRuntimeError(f"LLM 응답이 JSON 객체가 아님: {exc}") from exc
        try:
            value = json.loads(text[start:end + 1])
        except json.JSONDecodeError as nested:
            raise LLMRuntimeError(f"LLM JSON 파싱 실패: {nested}") from nested
    if not isinstance(value, dict):
        raise LLMRuntimeError("LLM 응답 최상위가 JSON 객체가 아님")
    return value


class OpenAICompatibleJsonClient:
    """Small stdlib-only client for hosted OpenAI-compatible endpoints."""

    def __init__(self, config: LLMConfig):
        self.config = config
        if config.mode == "required" and not config.available:
            raise LLMRuntimeError("llm-mode=required지만 LLM_API_URL/LLM_API_KEY/LLM_MODEL 설정이 없습니다.")

    def generate_json(self, system_prompt: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.config.available:
            raise LLMRuntimeError("사용 가능한 LLM endpoint가 없습니다.")
        body = {
            "model": self.config.model,
            "temperature": 0,
            "max_tokens": self.config.max_output_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        request = Request(
            _chat_url(self.config.endpoint or ""),
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.config.timeout_s) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise LLMRuntimeError(f"LLM HTTP 오류 {exc.code}: {detail}") from exc
        except URLError as exc:
            raise LLMRuntimeError(f"LLM 네트워크 오류: {exc.reason}") from exc
        except TimeoutError as exc:
            raise LLMRuntimeError("LLM 요청 timeout") from exc

        try:
            response_json = json.loads(raw)
            content = response_json["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise LLMRuntimeError(f"LLM 응답 형식 오류: {exc}") from exc
        return _extract_json(content)

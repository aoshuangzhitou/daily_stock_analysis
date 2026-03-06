# -*- coding: utf-8 -*-
"""
Multi-provider LLM Tool-Calling Adapter.

Normalizes function-calling / tool-use across all providers into a unified
interface consumed by the AgentExecutor, via LiteLLM.
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from openai import OpenAI

from src.config import get_config, get_api_keys_for_model, extra_litellm_params

logger = logging.getLogger(__name__)


# ============================================================
# Unified response types
# ============================================================

@dataclass
class ToolCall:
    """A single tool call requested by the LLM."""
    id: str
    name: str
    arguments: Dict[str, Any]
    thought_signature: Optional[str] = None


@dataclass
class LLMResponse:
    """Normalized response from any LLM provider."""
    content: Optional[str] = None          # text response (final answer)
    tool_calls: List[ToolCall] = field(default_factory=list)  # tool calls to execute
    reasoning_content: Optional[str] = None  # Chain-of-thought (CoT) from DeepSeek thinking mode; must be passed back in multi-turn assistant messages; None for other providers
    usage: Dict[str, Any] = field(default_factory=dict)       # token usage info
    provider: str = ""                     # which provider handled this call
    raw: Any = None                        # raw provider response for debugging


# Models that auto-return reasoning_content; do NOT send extra_body (may cause 400).
_AUTO_THINKING_MODELS: List[str] = ["deepseek-reasoner", "deepseek-r1", "qwq"]

# Models that need explicit opt-in via extra_body; payload decoupled from model name.
_OPT_IN_THINKING_MODELS: Dict[str, dict] = {
    "deepseek-chat": {"thinking": {"type": "enabled"}},
}


def _model_matches(model: str, entries: List[str]) -> bool:
    """Check if model name matches any entry (exact or prefix with version suffix)."""
    if not model:
        return False
    m = model.lower().strip()
    for e in entries:
        if m == e or m.startswith(e + "-"):
            return True
    return False


def _get_opt_in_payload(model: str, opt_in: Dict[str, dict]) -> Optional[dict]:
    """Return extra_body payload for opt-in thinking models, or None."""
    if not model:
        return None
    m = model.lower().strip()
    for key, payload in opt_in.items():
        if m == key or m.startswith(key + "-"):
            return payload
    return None


def get_thinking_extra_body(model: str) -> Optional[dict]:
    """Return extra_body for thinking mode, or None.

    - Auto-thinking models (_AUTO_THINKING_MODELS: deepseek-reasoner, deepseek-r1, qwq):
      These models automatically return reasoning_content in API responses; sending
      extra_body would cause 400 because the API already enables thinking by default.
      Return None to avoid duplicate activation.
    - Opt-in models (_OPT_IN_THINKING_MODELS: deepseek-chat): Return the activation
      payload to explicitly enable thinking mode.
    - All other models: Return None (no thinking mode).
    """
    if _model_matches(model, _AUTO_THINKING_MODELS):
        return None
    return _get_opt_in_payload(model, _OPT_IN_THINKING_MODELS)


# ============================================================
# LLM Tool Adapter
# ============================================================

class LLMToolAdapter:
    """Unified adapter for tool-calling via OpenAI SDK.

    Uses OpenAI SDK directly to call OpenAI-compatible APIs.
    """

    def __init__(self, config=None):
        config = config or get_config()
        self._config = config
        self._openai_available = False
        self._init_openai()

    def _get_openai_key(self) -> Optional[str]:
        """Return the first available OpenAI API key."""
        keys = [k for k in self._config.openai_api_keys if k and len(k) >= 8]
        return keys[0] if keys else None

    def _get_openai_client_kwargs(self) -> Dict[str, Any]:
        """Build kwargs for OpenAI client initialization."""
        kwargs: Dict[str, Any] = {}
        if self._config.openai_base_url:
            kwargs["base_url"] = self._config.openai_base_url
        if self._config.openai_base_url and "aihubmix.com" in self._config.openai_base_url:
            kwargs["default_headers"] = {"APP-Code": "GPIJ3886"}
        return kwargs

    def _init_openai(self) -> None:
        """Initialize OpenAI client availability check."""
        config = self._config

        # Check for OpenAI API keys and base_url
        if not self._get_openai_key():
            logger.warning("Agent LLM: No OPENAI_API_KEYS configured")
            return

        if not config.openai_base_url:
            logger.warning("Agent LLM: No OPENAI_BASE_URL configured")
            return

        self._openai_available = True
        logger.info(f"Agent LLM: OpenAI SDK initialized (base_url={config.openai_base_url})")

    @property
    def is_available(self) -> bool:
        """True if OpenAI SDK is configured and at least one API key is present."""
        return self._openai_available

    @property
    def primary_provider(self) -> str:
        """Provider name (always 'openai' for OpenAI SDK)."""
        return "openai"

    # ============================================================
    # Unified call
    # ============================================================

    def call_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[dict],
        provider: Optional[str] = None,
    ) -> LLMResponse:
        """Send messages + tool declarations to LLM, return normalized response.

        Args:
            messages: Conversation history in provider-neutral format:
                      [{"role": "system"/"user"/"assistant"/"tool", "content": ...}, ...]
            tools: OpenAI-format tool declarations.
            provider: Ignored (kept for backward compatibility).

        Returns:
            LLMResponse with either content (final answer) or tool_calls.
        """
        config = self._config

        # Get primary model and fallback models
        primary_model = config.openai_model
        models_to_try = [primary_model] if primary_model else []

        # Add legacy fallback models from config for backward compatibility
        if config.litellm_fallback_models:
            for m in config.litellm_fallback_models:
                model_name = m.split("/")[-1] if "/" in m else m
                if model_name not in models_to_try:
                    models_to_try.append(model_name)

        if not models_to_try:
            error_msg = "No OpenAI model configured (OPENAI_MODEL)"
            logger.error(error_msg)
            return LLMResponse(content=error_msg, provider="error")

        last_error = None
        for model in models_to_try:
            try:
                return self._call_openai_model(messages, tools, model)
            except Exception as e:
                logger.warning(f"Agent LLM call failed with {model}: {e}")
                last_error = e
                continue

        error_msg = f"All LLM models failed. Last error: {last_error}"
        logger.error(error_msg)
        return LLMResponse(content=error_msg, provider="error")

    def _call_openai_model(
        self,
        messages: List[Dict[str, Any]],
        tools: List[dict],
        model: str,
    ) -> LLMResponse:
        """Call OpenAI SDK with OpenAI-format messages and tools."""
        openai_messages = self._convert_messages(messages)

        # Use short model name (without provider prefix) for thinking model lookup
        model_short = model.split("/")[-1] if "/" in model else model

        call_kwargs: Dict[str, Any] = {
            "model": model_short,
            "messages": openai_messages,
            "temperature": self._config.openai_temperature,
        }

        extra = get_thinking_extra_body(model_short)
        if extra:
            call_kwargs["extra_body"] = extra

        if tools:
            call_kwargs["tools"] = tools

        api_key = self._get_openai_key()
        if not api_key:
            raise ValueError("No OpenAI API key configured")

        client_kwargs = self._get_openai_client_kwargs()
        client = OpenAI(api_key=api_key, **client_kwargs)

        response = client.chat.completions.create(**call_kwargs)
        return self._parse_openai_response(response, model_short)

    def _get_temperature(self, model: str) -> float:
        """Return temperature from config (always use openai_temperature now)."""
        return self._config.openai_temperature

    def _convert_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert internal message format to OpenAI-compatible format for litellm."""
        openai_messages: List[Dict[str, Any]] = []
        for msg in messages:
            if msg["role"] == "tool":
                openai_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.get("tool_call_id", ""),
                    "content": msg["content"] if isinstance(msg["content"], str) else json.dumps(msg["content"]),
                })
            elif msg["role"] == "assistant" and msg.get("tool_calls"):
                openai_tc = []
                for tc in msg["tool_calls"]:
                    tc_dict: Dict[str, Any] = {
                        "id": tc.get("id", str(uuid.uuid4())[:8]),
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"]),
                        },
                    }
                    sig = tc.get("thought_signature")
                    if sig is not None:
                        tc_dict["provider_specific_fields"] = {"thought_signature": sig}
                    openai_tc.append(tc_dict)
                openai_msg: Dict[str, Any] = {
                    "role": "assistant",
                    "content": msg.get("content"),
                    "tool_calls": openai_tc,
                }
                if msg.get("reasoning_content") is not None:
                    openai_msg["reasoning_content"] = msg["reasoning_content"]
                openai_messages.append(openai_msg)
            else:
                openai_messages.append({
                    "role": msg["role"],
                    "content": msg["content"],
                })
        return openai_messages

    def _parse_openai_response(self, response: Any, model: str) -> LLMResponse:
        """Parse OpenAI SDK response into LLMResponse."""
        choice = response.choices[0]
        tool_calls: List[ToolCall] = []
        text_content = choice.message.content
        # DeepSeek/Qwen thinking mode; not in standard OpenAI type, accessed via getattr
        reasoning_content = getattr(choice.message, "reasoning_content", None)

        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                args: Dict[str, Any] = {}
                if tc.function.arguments:
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = {"raw": tc.function.arguments}

                # Extract thought_signature: stored in provider_specific_fields if available
                psf = getattr(tc, "provider_specific_fields", None)
                if psf is not None:
                    sig = psf.get("thought_signature") if isinstance(psf, dict) else getattr(psf, "thought_signature", None)
                else:
                    func_psf = getattr(tc.function, "provider_specific_fields", None)
                    if func_psf is not None:
                        sig = func_psf.get("thought_signature") if isinstance(func_psf, dict) else getattr(func_psf, "thought_signature", None)
                    else:
                        sig = getattr(tc, "thought_signature", None)

                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                    thought_signature=sig,
                ))

        usage: Dict[str, Any] = {}
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        return LLMResponse(
            content=text_content,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
            usage=usage,
            provider="openai",
            raw=response,
        )

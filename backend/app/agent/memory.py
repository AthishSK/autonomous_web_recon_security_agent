"""
memory.py — Conversation and tool-result memory for the ReAct agent.

Responsibilities:
  1. ConversationMemory  — stores the full message history sent to the LLM.
  2. ToolResultCache     — deduplicates identical tool calls within a session.
  3. StepLog             — structured audit trail of every ReAct step taken.
  4. ContextWindowManager— trims conversation to stay within token limits.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.core.logger import get_logger

logger = get_logger(__name__)

# Rough chars-per-token ratio for token estimation (conservative)
_CHARS_PER_TOKEN = 3.5


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Message:
    role: str          # "system" | "user" | "assistant"
    content: str
    timestamp: float = field(default_factory=time.time)

    def to_api_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class ToolCall:
    tool: str
    input: dict[str, Any]
    result: str
    status: str        # "success" | "error"
    duration_ms: float
    timestamp: float = field(default_factory=time.time)

    @property
    def cache_key(self) -> str:
        payload = json.dumps({"tool": self.tool, "input": self.input}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class ReActStep:
    step_number: int
    thought: str | None
    action_tool: str | None
    action_input: dict[str, Any] | None
    observation: str | None
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Conversation Memory
# ---------------------------------------------------------------------------

class ConversationMemory:
    """
    Maintains the ordered list of messages for the LLM conversation.

    Roles follow the Anthropic / OpenAI convention:
      - "system"    : system prompt (injected once, at position 0)
      - "user"      : user turns + tool observation injections
      - "assistant" : raw LLM responses
    """

    def __init__(self, system_prompt: str = "") -> None:
        self._messages: list[Message] = []
        if system_prompt:
            self.set_system_prompt(system_prompt)

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def set_system_prompt(self, content: str) -> None:
        """Replace (or set) the system message at position 0."""
        system_msg = Message(role="system", content=content)
        if self._messages and self._messages[0].role == "system":
            self._messages[0] = system_msg
        else:
            self._messages.insert(0, system_msg)

    def add_user(self, content: str) -> None:
        self._messages.append(Message(role="user", content=content))

    def add_assistant(self, content: str) -> None:
        self._messages.append(Message(role="assistant", content=content))

    def add_tool_observation(self, observation: str) -> None:
        """Inject a tool result as a user-role message (standard ReAct pattern)."""
        self._messages.append(Message(role="user", content=observation))

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    @property
    def messages(self) -> list[Message]:
        return list(self._messages)

    def to_api_messages(self) -> list[dict[str, str]]:
        """Return messages formatted for the LLM API (excluding system for APIs
        that handle system separately, like Anthropic's)."""
        return [m.to_api_dict() for m in self._messages if m.role != "system"]

    @property
    def system_prompt(self) -> str:
        if self._messages and self._messages[0].role == "system":
            return self._messages[0].content
        return ""

    @property
    def turn_count(self) -> int:
        """Number of assistant turns taken so far."""
        return sum(1 for m in self._messages if m.role == "assistant")

    def last_assistant_message(self) -> str | None:
        for m in reversed(self._messages):
            if m.role == "assistant":
                return m.content
        return None

    # ------------------------------------------------------------------
    # Token estimation & trimming
    # ------------------------------------------------------------------

    def estimated_tokens(self) -> int:
        total_chars = sum(len(m.content) for m in self._messages)
        return int(total_chars / _CHARS_PER_TOKEN)

    def trim_to_token_limit(
        self,
        max_tokens: int = 90_000,
        keep_system: bool = True,
        keep_last_n: int = 6,
    ) -> int:
        """
        Remove older messages to stay under max_tokens.
        Always keeps the system prompt and the last `keep_last_n` messages.
        Returns the number of messages removed.
        """
        if self.estimated_tokens() <= max_tokens:
            return 0

        protected_indices: set[int] = set()
        if keep_system and self._messages and self._messages[0].role == "system":
            protected_indices.add(0)
        for i in range(max(0, len(self._messages) - keep_last_n), len(self._messages)):
            protected_indices.add(i)

        removed = 0
        new_messages: list[Message] = []
        for idx, msg in enumerate(self._messages):
            if idx in protected_indices:
                new_messages.append(msg)
            else:
                removed += 1

            if self._estimated_tokens_for(new_messages) <= max_tokens:
                # Add all remaining protected messages and stop
                for remaining_idx in range(idx + 1, len(self._messages)):
                    if remaining_idx in protected_indices:
                        new_messages.append(self._messages[remaining_idx])
                break
        else:
            pass  # loop completed without early exit

        if removed:
            logger.info(
                "Trimmed %d messages from conversation memory (token limit: %d)",
                removed, max_tokens,
            )
        self._messages = new_messages
        return removed

    @staticmethod
    def _estimated_tokens_for(messages: list[Message]) -> int:
        total_chars = sum(len(m.content) for m in messages)
        return int(total_chars / _CHARS_PER_TOKEN)

    def clear(self, keep_system: bool = True) -> None:
        if keep_system and self._messages and self._messages[0].role == "system":
            self._messages = [self._messages[0]]
        else:
            self._messages = []


# ---------------------------------------------------------------------------
# Tool Result Cache
# ---------------------------------------------------------------------------

class ToolResultCache:
    """
    In-memory cache for tool results within a single agent session.

    Prevents redundant calls for identical (tool, input) pairs.
    Respects per-tool TTL and a maximum cache size.
    """

    DEFAULT_TTL: float = 300.0  # seconds

    def __init__(
        self,
        max_size: int = 128,
        default_ttl: float = DEFAULT_TTL,
        tool_ttl_overrides: dict[str, float] | None = None,
    ) -> None:
        self._store: dict[str, tuple[str, float]] = {}  # key → (result, expires_at)
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._tool_ttl: dict[str, float] = tool_ttl_overrides or {}
        self._hit_counts: dict[str, int] = defaultdict(int)

    def _make_key(self, tool: str, input_data: dict) -> str:
        payload = json.dumps({"tool": tool, "input": input_data}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    def _ttl_for(self, tool: str) -> float:
        return self._tool_ttl.get(tool, self._default_ttl)

    def get(self, tool: str, input_data: dict) -> str | None:
        key = self._make_key(tool, input_data)
        entry = self._store.get(key)
        if entry is None:
            return None
        result, expires_at = entry
        if time.time() > expires_at:
            del self._store[key]
            logger.debug("Cache expired for tool=%s", tool)
            return None
        self._hit_counts[key] += 1
        logger.debug("Cache hit for tool=%s (hits=%d)", tool, self._hit_counts[key])
        return result

    def set(self, tool: str, input_data: dict, result: str) -> None:
        if len(self._store) >= self._max_size:
            self._evict_oldest()
        key = self._make_key(tool, input_data)
        expires_at = time.time() + self._ttl_for(tool)
        self._store[key] = (result, expires_at)
        logger.debug("Cached result for tool=%s (ttl=%.0fs)", tool, self._ttl_for(tool))

    def invalidate(self, tool: str | None = None) -> int:
        """Remove cache entries — all if tool is None, otherwise just that tool's entries."""
        if tool is None:
            count = len(self._store)
            self._store.clear()
            return count
        # We can't reverse-lookup by tool without storing metadata, so re-hash all known
        # In practice this path is rarely needed; full clear is the common use case.
        logger.warning("Per-tool invalidation not supported; use invalidate() without arguments.")
        return 0

    def _evict_oldest(self) -> None:
        """Remove the entry with the earliest expiry time."""
        if not self._store:
            return
        oldest_key = min(self._store, key=lambda k: self._store[k][1])
        del self._store[oldest_key]
        logger.debug("Evicted oldest cache entry (size limit reached)")

    @property
    def size(self) -> int:
        return len(self._store)

    @property
    def hit_counts(self) -> dict[str, int]:
        return dict(self._hit_counts)


# ---------------------------------------------------------------------------
# Step Log (audit trail)
# ---------------------------------------------------------------------------

class StepLog:
    """
    Records every ReAct step taken during a session for auditing and
    post-run analysis (e.g., building the final report metadata).
    """

    def __init__(self) -> None:
        self._steps: list[ReActStep] = []
        self._tool_calls: list[ToolCall] = []
        self._session_start: float = time.time()

    def record_step(
        self,
        thought: str | None = None,
        action_tool: str | None = None,
        action_input: dict | None = None,
        observation: str | None = None,
    ) -> ReActStep:
        step = ReActStep(
            step_number=len(self._steps) + 1,
            thought=thought,
            action_tool=action_tool,
            action_input=action_input,
            observation=observation,
        )
        self._steps.append(step)
        logger.debug("Recorded step %d (tool=%s)", step.step_number, action_tool or "—")
        return step

    def record_tool_call(
        self,
        tool: str,
        input_data: dict,
        result: str,
        status: str,
        duration_ms: float,
    ) -> ToolCall:
        tc = ToolCall(
            tool=tool,
            input=input_data,
            result=result,
            status=status,
            duration_ms=duration_ms,
        )
        self._tool_calls.append(tc)
        return tc

    @property
    def steps(self) -> list[ReActStep]:
        return list(self._steps)

    @property
    def tool_calls(self) -> list[ToolCall]:
        return list(self._tool_calls)

    @property
    def step_count(self) -> int:
        return len(self._steps)

    @property
    def session_duration_seconds(self) -> float:
        return round(time.time() - self._session_start, 2)

    def tools_used(self) -> list[str]:
        return list({tc.tool for tc in self._tool_calls})

    def to_summary_dict(self) -> dict:
        return {
            "total_steps": self.step_count,
            "tools_used": self.tools_used(),
            "tool_call_count": len(self._tool_calls),
            "session_duration_seconds": self.session_duration_seconds,
            "errors": sum(1 for tc in self._tool_calls if tc.status == "error"),
        }

    def clear(self) -> None:
        self._steps.clear()
        self._tool_calls.clear()
        self._session_start = time.time()


# ---------------------------------------------------------------------------
# Unified AgentMemory facade
# ---------------------------------------------------------------------------

class AgentMemory:
    """
    Single entry point for all memory concerns in the agent.

    Combines ConversationMemory, ToolResultCache, and StepLog under
    one object so the orchestrator doesn't need to juggle them separately.
    """

    def __init__(
        self,
        system_prompt: str = "",
        max_context_tokens: int = 90_000,
        cache_ttl: float = ToolResultCache.DEFAULT_TTL,
        tool_ttl_overrides: dict[str, float] | None = None,
    ) -> None:
        self.conversation = ConversationMemory(system_prompt=system_prompt)
        self.cache = ToolResultCache(
            default_ttl=cache_ttl,
            tool_ttl_overrides=tool_ttl_overrides,
        )
        self.log = StepLog()
        self._max_context_tokens = max_context_tokens

    # ------------------------------------------------------------------
    # Convenience pass-throughs
    # ------------------------------------------------------------------

    def add_user_message(self, content: str) -> None:
        self.conversation.add_user(content)
        self._maybe_trim()

    def add_assistant_message(self, content: str) -> None:
        self.conversation.add_assistant(content)

    def inject_observation(self, observation: str) -> None:
        self.conversation.add_tool_observation(observation)
        self._maybe_trim()

    def get_cached_result(self, tool: str, input_data: dict) -> str | None:
        return self.cache.get(tool, input_data)

    def store_tool_result(self, tool: str, input_data: dict, result: str) -> None:
        self.cache.set(tool, input_data, result)

    # ------------------------------------------------------------------
    # Context management
    # ------------------------------------------------------------------

    def _maybe_trim(self) -> None:
        if self.conversation.estimated_tokens() > self._max_context_tokens:
            removed = self.conversation.trim_to_token_limit(self._max_context_tokens)
            if removed:
                logger.info("Auto-trimmed %d messages to manage context window", removed)

    @property
    def turn_count(self) -> int:
        return self.conversation.turn_count

    @property
    def session_summary(self) -> dict:
        return {
            **self.log.to_summary_dict(),
            "conversation_turns": self.conversation.turn_count,
            "estimated_context_tokens": self.conversation.estimated_tokens(),
            "cache_size": self.cache.size,
        }

    def reset(self, keep_system_prompt: bool = True) -> None:
        self.conversation.clear(keep_system=keep_system_prompt)
        self.cache.invalidate()
        self.log.clear()
        logger.info("AgentMemory reset (keep_system=%s)", keep_system_prompt)
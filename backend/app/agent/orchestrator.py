"""
orchestrator.py — Main ReAct agent loop.

Flow per iteration:
  1. Build messages from AgentMemory
  2. Call the LLM (Ollama local — /api/generate)
  3. Parse response → thought / action / final_answer
  4. If action: execute tool, inject observation, loop
  5. If final_answer: return completed report
  6. If max_steps reached: force wrap-up

Supports async Server-Sent Events (SSE) streaming via an async generator.

LLM backend: Ollama  (http://localhost:11434/api/generate)
Default model: deepseek-r1:latest
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, AsyncGenerator

import httpx

from app.agent.memory import AgentMemory
from app.agent.parser import ParsedResponse, parse_llm_response, validate_action
from app.agent.prompts import (
    FINAL_REPORT_HINT,
    build_initial_task,
    build_system_prompt,
    build_tool_error_message,
    build_tool_result_message,
)
from app.core.config import settings
from app.core.logger import get_logger
from app.tools.registry import ToolRegistry

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# DeepSeek-R1 emits <think>...</think> for its internal chain-of-thought.
# We strip those blocks before handing text to our parser so they don't
# interfere with the <thought> / <action> / <final_answer> tags.
# The raw thinking text is preserved separately for streaming.
# ---------------------------------------------------------------------------
_THINK_TAG_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)


def _split_deepseek_response(text: str) -> tuple[str, str]:
    """
    Split a DeepSeek-R1 response into (thinking_text, agent_text).

    - thinking_text : content inside <think>...</think> (internal reasoning)
    - agent_text    : everything outside those tags (the ReAct-formatted reply)
    """
    thinking_parts: list[str] = []

    def _capture(m: re.Match) -> str:
        thinking_parts.append(m.group(1).strip())
        return ""  # remove from agent_text

    agent_text = _THINK_TAG_RE.sub(_capture, text).strip()
    thinking_text = "\n\n".join(thinking_parts)
    return thinking_text, agent_text


# ---------------------------------------------------------------------------
# Prompt assembly
# Ollama /api/generate is a single-turn endpoint; we flatten the full
# conversation history into one prompt string using a simple INST template.
# ---------------------------------------------------------------------------

_ROLE_PREFIX = {
    "system": "",           # system is passed separately in the request body
    "user":   "USER: ",
    "assistant": "ASSISTANT: ",
}


def _build_ollama_prompt(messages: list[dict[str, str]]) -> str:
    """
    Flatten a list of role/content dicts into a single prompt string
    suitable for Ollama /api/generate.

    System messages are excluded here — they are passed via the `system`
    field in the request body instead.
    """
    parts: list[str] = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"].strip()
        if role == "system":
            continue  # handled separately
        prefix = _ROLE_PREFIX.get(role, f"{role.upper()}: ")
        parts.append(f"{prefix}{content}")

    # Append the prompt suffix so the model knows it should reply
    parts.append("ASSISTANT: ")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# SSE event helper
# ---------------------------------------------------------------------------

def _sse_event(event_type: str, data: Any) -> dict:
    return {"event": event_type, "data": data}


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class AgentOrchestrator:
    """
    Drives a single ReAct session from initial task to final report,
    using a local Ollama model via /api/generate.

    Usage (streaming):
        async for event in orchestrator.run_stream(target="example.com"):
            yield f"data: {json.dumps(event)}\\n\\n"

    Usage (blocking):
        report = await orchestrator.run(target="example.com")
    """

    OLLAMA_DEFAULT_URL   = "http://localhost:11434/api/generate"
    OLLAMA_DEFAULT_MODEL = "deepseek-r1:latest"

    def __init__(
        self,
        tool_registry: ToolRegistry,
        ollama_url: str | None = None,
        model: str | None = None,
        max_steps: int = 20,
        max_context_tokens: int = 90_000,
        request_timeout: float = 180.0,   # local models can be slow
        cache_ttl: float = 300.0,
        ollama_options: dict | None = None,
    ) -> None:
        self._registry         = tool_registry
        self._ollama_url       = ollama_url or getattr(settings, "OLLAMA_URL", self.OLLAMA_DEFAULT_URL)
        self._model            = model      or getattr(settings, "OLLAMA_MODEL", self.OLLAMA_DEFAULT_MODEL)
        self._max_steps        = max_steps
        self._max_context_tokens = max_context_tokens
        self._request_timeout  = request_timeout
        self._cache_ttl        = cache_ttl
        # Extra Ollama options forwarded verbatim (temperature, top_p, etc.)
        self._ollama_options: dict = ollama_options or {
            "temperature": 0.2,       # low temp for deterministic tool calls
            "top_p": 0.9,
            "num_ctx": 16384,         # context window — adjust for your hardware
        }
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(request_timeout))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        target: str,
        scope: str = "Full external reconnaissance",
        objectives: list[str] | None = None,
        context: str = "No additional context provided.",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Blocking run — collects the stream and returns the final report dict."""
        report: dict[str, Any] = {}
        async for event in self.run_stream(
            target=target,
            scope=scope,
            objectives=objectives,
            context=context,
            session_id=session_id,
        ):
            if event["event"] == "final_answer":
                report = event["data"].get("report", {})
        return report

    async def run_stream(
        self,
        target: str,
        scope: str = "Full external reconnaissance",
        objectives: list[str] | None = None,
        context: str = "No additional context provided.",
        session_id: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        """
        Async generator that yields SSE event dicts throughout the ReAct loop.

        Event types:
          - "session_start"    : session metadata
          - "model_thinking"   : DeepSeek-R1 internal <think> content (stripped)
          - "thought"          : agent <thought> block
          - "action"           : tool call about to be dispatched
          - "tool_result"      : raw tool output (success or error)
          - "step_complete"    : end of one ReAct step
          - "final_answer"     : finished report + session summary
          - "error"            : unrecoverable error
          - "max_steps"        : forced termination at step limit
        """
        session_id = session_id or f"session-{int(time.time())}"
        logger.info(
            "Starting agent session %s | target=%s | model=%s",
            session_id, target, self._model,
        )

        # ---- initialise memory ----
        tool_descriptions = self._registry.get_tool_descriptions()
        system_prompt     = build_system_prompt(tool_descriptions)
        memory = AgentMemory(
            system_prompt=system_prompt,
            max_context_tokens=self._max_context_tokens,
            cache_ttl=self._cache_ttl,
        )

        initial_task = build_initial_task(
            target=target,
            scope=scope,
            objectives=objectives,
            context=context,
        )
        memory.add_user_message(initial_task)

        yield _sse_event("session_start", {
            "session_id": session_id,
            "target":     target,
            "scope":      scope,
            "max_steps":  self._max_steps,
            "model":      self._model,
            "ollama_url": self._ollama_url,
        })

        registered_tools = set(self._registry.tool_names())

        # ====================================================================
        # ReAct loop
        # ====================================================================
        for step in range(1, self._max_steps + 1):
            logger.info("[%s] Step %d / %d", session_id, step, self._max_steps)

            # ---- call LLM ----
            try:
                raw_text = await self._call_llm(memory)
            except Exception as exc:
                logger.exception("LLM call failed at step %d", step)
                yield _sse_event("error", {"step": step, "message": str(exc)})
                return

            # ---- strip DeepSeek-R1 <think> blocks ----
            thinking_text, agent_text = _split_deepseek_response(raw_text)

            if thinking_text:
                logger.debug("[%s] Model thinking (%d chars)", session_id, len(thinking_text))
                yield _sse_event("model_thinking", {
                    "step": step,
                    "content": thinking_text,
                })

            # Store only the clean agent_text in conversation memory
            memory.add_assistant_message(agent_text)

            # ---- parse agent reply ----
            parsed: ParsedResponse = parse_llm_response(agent_text)

            # Stream thought
            if parsed.thought:
                memory.log.record_step(thought=parsed.thought.content)
                yield _sse_event("thought", {
                    "step":    step,
                    "content": parsed.thought.content,
                })

            # ---- final answer ----
            if parsed.is_complete:
                fa = parsed.final_answer
                logger.info("[%s] Final answer received at step %d", session_id, step)
                memory.log.record_step(
                    thought=parsed.thought.content if parsed.thought else None,
                    observation="[final answer]",
                )
                yield _sse_event("final_answer", {
                    "step":    step,
                    "report":  fa.report,
                    "session": memory.session_summary,
                })
                return

            # ---- action ----
            if parsed.has_action:
                action = parsed.action  # type: ignore[assignment]

                is_valid, error_msg = validate_action(action, registered_tools)
                if not is_valid:
                    logger.warning("[%s] Invalid action at step %d: %s", session_id, step, error_msg)
                    memory.inject_observation(
                        build_tool_error_message(action.tool or "unknown", error_msg)
                    )
                    yield _sse_event("tool_result", {
                        "step":   step,
                        "tool":   action.tool,
                        "status": "error",
                        "result": error_msg,
                    })
                    continue

                yield _sse_event("action", {
                    "step":  step,
                    "tool":  action.tool,
                    "input": action.input,
                })

                # Cache check
                cached = memory.get_cached_result(action.tool, action.input)
                if cached is not None:
                    logger.info("[%s] Cache hit for tool=%s", session_id, action.tool)
                    tool_result, tool_status, duration_ms = cached, "success", 0.0
                else:
                    tool_result, tool_status, duration_ms = await self._execute_tool(
                        action.tool, action.input
                    )
                    if tool_status == "success":
                        memory.store_tool_result(action.tool, action.input, tool_result)

                memory.log.record_tool_call(
                    tool=action.tool,
                    input_data=action.input,
                    result=tool_result,
                    status=tool_status,
                    duration_ms=duration_ms,
                )
                memory.log.record_step(
                    thought=parsed.thought.content if parsed.thought else None,
                    action_tool=action.tool,
                    action_input=action.input,
                    observation=tool_result[:500],
                )

                yield _sse_event("tool_result", {
                    "step":        step,
                    "tool":        action.tool,
                    "status":      tool_status,
                    "duration_ms": round(duration_ms, 1),
                    "result":      tool_result,
                })

                obs = (
                    build_tool_result_message(action.tool, tool_result)
                    if tool_status == "success"
                    else build_tool_error_message(action.tool, tool_result)
                )
                memory.inject_observation(obs)
                yield _sse_event("step_complete", {"step": step, "tool": action.tool})

            else:
                logger.warning(
                    "[%s] No action or final_answer at step %d — nudging model",
                    session_id, step,
                )
                memory.add_user_message(
                    "Your last response did not contain a valid <action> or <final_answer> block. "
                    "Please continue the ReAct loop or produce a final answer."
                )

        # ====================================================================
        # Max steps reached
        # ====================================================================
        logger.warning("[%s] Max steps (%d) reached — forcing final answer", session_id, self._max_steps)
        memory.add_user_message(FINAL_REPORT_HINT)

        try:
            raw_text = await self._call_llm(memory)
        except Exception as exc:
            yield _sse_event("max_steps", {
                "message": "Max steps reached; wrap-up LLM call failed.",
                "error": str(exc),
            })
            return

        _, agent_text = _split_deepseek_response(raw_text)
        memory.add_assistant_message(agent_text)
        parsed = parse_llm_response(agent_text)

        if parsed.is_complete:
            yield _sse_event("final_answer", {
                "step":    self._max_steps,
                "report":  parsed.final_answer.report,  # type: ignore[union-attr]
                "session": memory.session_summary,
                "forced":  True,
            })
        else:
            yield _sse_event("max_steps", {
                "message":      "Max steps reached and no final answer produced.",
                "partial_text": agent_text[:1000],
                "session":      memory.session_summary,
            })

    # ------------------------------------------------------------------
    # Ollama /api/generate call
    # ------------------------------------------------------------------

    async def _call_llm(self, memory: AgentMemory) -> str:
        """
        Send the full conversation to Ollama /api/generate and return
        the complete response text (non-streaming).
        """
        api_messages  = memory.conversation.to_api_messages()
        system_prompt = memory.conversation.system_prompt
        prompt        = _build_ollama_prompt(api_messages)

        payload: dict[str, Any] = {
            "model":   self._model,
            "prompt":  prompt,
            "system":  system_prompt,
            "stream":  False,
            "options": self._ollama_options,
        }

        logger.debug(
            "Ollama request | model=%s | prompt_chars=%d | estimated_tokens=%d",
            self._model,
            len(prompt),
            memory.conversation.estimated_tokens(),
        )

        try:
            response = await self._http.post(self._ollama_url, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Ollama HTTP error {exc.response.status_code}: {exc.response.text[:300]}"
            ) from exc
        except httpx.ConnectError:
            raise RuntimeError(
                f"Cannot connect to Ollama at {self._ollama_url}. "
                "Is 'ollama serve' running?"
            )

        data = response.json()

        # Ollama /api/generate returns {"response": "...", "done": true, ...}
        text = data.get("response", "").strip()
        if not text:
            raise RuntimeError(
                f"Ollama returned an empty response. Full payload: {json.dumps(data)[:400]}"
            )

        logger.debug(
            "Ollama response received | chars=%d | eval_tokens=%s",
            len(text),
            data.get("eval_count", "?"),
        )
        return text

    # ------------------------------------------------------------------
    # Tool execution (unchanged from original)
    # ------------------------------------------------------------------

    async def _execute_tool(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> tuple[str, str, float]:
        """Execute a tool and return (result_str, status, duration_ms)."""
        start = time.perf_counter()
        try:
            tool_fn = self._registry.get_tool(tool_name)
            logger.info("Executing tool: %s | input=%s", tool_name, json.dumps(tool_input)[:200])

            if asyncio.iscoroutinefunction(tool_fn):
                result = await asyncio.wait_for(
                    tool_fn(**tool_input),
                    timeout=self._request_timeout,
                )
            else:
                loop = asyncio.get_event_loop()
                result = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: tool_fn(**tool_input)),
                    timeout=self._request_timeout,
                )

            duration_ms = (time.perf_counter() - start) * 1000
            result_str  = result if isinstance(result, str) else json.dumps(result, default=str)
            logger.info("Tool %s completed in %.1f ms", tool_name, duration_ms)
            return result_str, "success", duration_ms

        except asyncio.TimeoutError:
            duration_ms = (time.perf_counter() - start) * 1000
            msg = f"Tool '{tool_name}' timed out after {self._request_timeout:.0f}s"
            logger.error(msg)
            return msg, "error", duration_ms

        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            msg = f"Tool '{tool_name}' raised {type(exc).__name__}: {exc}"
            logger.exception("Tool execution error: %s", tool_name)
            return msg, "error", duration_ms

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying HTTP client. Call on app shutdown."""
        await self._http.aclose()
"""
parser.py — Parse raw LLM text responses into structured ReAct components.

Extracts:
  - <thought>   blocks  → reasoning text
  - <action>    blocks  → tool name + input dict
  - <observation> blocks → tool result echoes
  - <final_answer> blocks → completed report JSON
"""

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.core.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class StepType(str, Enum):
    THOUGHT = "thought"
    ACTION = "action"
    OBSERVATION = "observation"
    FINAL_ANSWER = "final_answer"
    UNKNOWN = "unknown"


@dataclass
class ThoughtBlock:
    content: str


@dataclass
class ActionBlock:
    tool: str
    input: dict[str, Any] = field(default_factory=dict)
    raw: str = ""


@dataclass
class ObservationBlock:
    content: str


@dataclass
class FinalAnswerBlock:
    report: dict[str, Any] = field(default_factory=dict)
    raw: str = ""


@dataclass
class ParsedResponse:
    thought: ThoughtBlock | None = None
    action: ActionBlock | None = None
    observation: ObservationBlock | None = None
    final_answer: FinalAnswerBlock | None = None
    raw_text: str = ""

    @property
    def step_type(self) -> StepType:
        if self.final_answer is not None:
            return StepType.FINAL_ANSWER
        if self.action is not None:
            return StepType.ACTION
        if self.observation is not None:
            return StepType.OBSERVATION
        if self.thought is not None:
            return StepType.THOUGHT
        return StepType.UNKNOWN

    @property
    def is_complete(self) -> bool:
        return self.final_answer is not None

    @property
    def has_action(self) -> bool:
        return self.action is not None


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_TAG_PATTERNS: dict[str, re.Pattern] = {
    "thought":      re.compile(r"<thought>(.*?)</thought>", re.DOTALL | re.IGNORECASE),
    "action":       re.compile(r"<action>(.*?)</action>", re.DOTALL | re.IGNORECASE),
    "observation":  re.compile(r"<observation>(.*?)</observation>", re.DOTALL | re.IGNORECASE),
    "final_answer": re.compile(r"<final_answer>(.*?)</final_answer>", re.DOTALL | re.IGNORECASE),
}

# Matches optional ```json fences inside action/final_answer blocks
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_tag(text: str, tag: str) -> str | None:
    """Return the first match of a tag block, stripped, or None."""
    match = _TAG_PATTERNS[tag].search(text)
    if match:
        return match.group(1).strip()
    return None


def _clean_json_text(raw: str) -> str:
    """Strip markdown code fences and surrounding whitespace from JSON text."""
    fence_match = _JSON_FENCE_RE.search(raw)
    if fence_match:
        return fence_match.group(1).strip()
    return raw.strip()


def _parse_json_block(raw: str, context: str = "") -> dict[str, Any] | None:
    """
    Attempt to parse a JSON string, with progressively looser fallbacks.
    Returns the parsed dict or None on failure.
    """
    cleaned = _clean_json_text(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.warning("JSON parse failed for %s block: %s", context, exc)

    # Fallback: try to find first {...} blob
    brace_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    logger.error("Could not parse JSON from %s block. Raw:\n%s", context, raw[:400])
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_llm_response(text: str) -> ParsedResponse:
    """
    Parse a full LLM response string into a structured ParsedResponse.

    Handles:
    - <thought> ... </thought>
    - <action> { "tool": ..., "input": {...} } </action>
    - <observation> ... </observation>   (rare — usually injected by orchestrator)
    - <final_answer> { ... } </final_answer>
    """
    response = ParsedResponse(raw_text=text)

    # --- thought ---
    thought_text = _extract_tag(text, "thought")
    if thought_text:
        response.thought = ThoughtBlock(content=thought_text)
        logger.debug("Parsed thought block (%d chars)", len(thought_text))

    # --- final_answer (check before action) ---
    fa_text = _extract_tag(text, "final_answer")
    if fa_text:
        report = _parse_json_block(fa_text, context="final_answer")
        response.final_answer = FinalAnswerBlock(
            report=report or {},
            raw=fa_text,
        )
        if not report:
            logger.warning("final_answer block present but JSON parsing failed")
        logger.debug("Parsed final_answer block")
        return response  # nothing more to do

    # --- action ---
    action_text = _extract_tag(text, "action")
    if action_text:
        action_data = _parse_json_block(action_text, context="action")
        if action_data:
            tool_name = action_data.get("tool", "").strip()
            tool_input = action_data.get("input", {})
            if not tool_name:
                logger.warning("Action block parsed but 'tool' key missing: %s", action_data)
            response.action = ActionBlock(
                tool=tool_name,
                input=tool_input if isinstance(tool_input, dict) else {},
                raw=action_text,
            )
            logger.debug("Parsed action: tool=%s input_keys=%s", tool_name, list(tool_input.keys()) if isinstance(tool_input, dict) else [])
        else:
            logger.warning("Action block found but could not parse JSON")

    # --- observation (echo from prior message, less common) ---
    obs_text = _extract_tag(text, "observation")
    if obs_text:
        response.observation = ObservationBlock(content=obs_text)

    if response.step_type == StepType.UNKNOWN:
        logger.warning("Could not identify any ReAct block in LLM response. Raw (first 300 chars):\n%s", text[:300])

    return response


def validate_action(action: ActionBlock, registered_tools: set[str]) -> tuple[bool, str]:
    """
    Validate that an action references a known tool with a non-empty name.

    Returns (is_valid, error_message).
    """
    if not action.tool:
        return False, "Action block has an empty 'tool' field."
    if action.tool not in registered_tools:
        available = ", ".join(sorted(registered_tools))
        return False, (
            f"Unknown tool '{action.tool}'. "
            f"Available tools: {available}"
        )
    return True, ""


def extract_all_thoughts(conversation: list[dict]) -> list[str]:
    """
    Utility: walk a conversation history and collect all thought block texts.
    Useful for summarization or memory distillation.
    """
    thoughts: list[str] = []
    for msg in conversation:
        if msg.get("role") == "assistant":
            thought_text = _extract_tag(msg.get("content", ""), "thought")
            if thought_text:
                thoughts.append(thought_text)
    return thoughts
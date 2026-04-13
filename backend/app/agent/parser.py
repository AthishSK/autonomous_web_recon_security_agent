import json, re

def parse_llm_output(text: str) -> dict:
    """
    Returns one of:
    {"type" : "though", "content":"..."}
    {"type" : "action", "tool" : "...", "args" : {...}}
    {"type": "final",   "report": {...}}
    """
    text = text.strip()

    #Extract Though line
    thought_match = re.search(r"Thought:\s*(.+?)(?=\nAction:|\nFinal Answer:|$)", text, re.DOTALL)
    thought = thought_match.group(1).strip() if thought_match else ""

    #Check for Final Answer
    final_match = re.search(r"Final Answer:\s*(\{.*\})", text, re.DOTALL)
    if final_match:
        try:
            report = json.loads(final_match.group(1))
            return {"type": "final", "thought": thought, "report": report}
        except json.JSONDecodeError:
            pass

    #Check for Action
    action_match = re.search(r"Action:\s*(\{.*?\})", text, re.DOTALL)
    if action_match:
        try:
            action = json.loads(action_match.group(1))
            return {
                "type": "action",
                "thought": thought,
                "tool": action["tool"],
                "args": action.get("args", {})
                }
        except(json.JSONDecodeError, KeyError):
            pass

    else:
        return ("type" : "thought", "content": thought or text)

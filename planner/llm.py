"""Two interchangeable "LLMs" with the same interface:

    reply = llm.chat(messages, tools)  ->  {"content": str | None,
                                            "tool_calls": [{"id", "name", "arguments"}]}

DeepSeekLLM  calls the real model through the OpenAI-compatible API (graded runs).
ScriptedLLM  replays a fixed, realistic policy offline: free, deterministic, used by the tests.
             It sends a first version of the code with a naive date parser, so on messy data you see
             a real traceback come back and a corrected version sent (a logged self-correction).
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

CODE_TEMPLATE = (Path(__file__).parent / "offline_code" / "plan_template.py").read_text(encoding="utf-8")

NAIVE_PARSER = '''def parse_due(raw):
    """First attempt: assumes every date is ISO 'YYYY-MM-DD HH:MM'."""
    return datetime.strptime(raw.strip(), "%Y-%m-%d %H:%M")
'''


def scheduling_code(params: dict, naive: bool = False) -> str:
    code = CODE_TEMPLATE.replace("__PARAMS__", json.dumps(params, indent=4))
    if naive:
        code = re.sub(r"# --- parse_due ---.*?# --- end parse_due ---", NAIVE_PARSER, code, flags=re.S)
    return code


# ====================================================================== real model
class DeepSeekLLM:
    def __init__(self, model: str | None = None, temperature: float = 0.0):
        from openai import OpenAI
        key = os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise RuntimeError("Set DEEPSEEK_API_KEY in your environment (never paste it in the code).")
        self.client = OpenAI(api_key=key, base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
        self.model = model or os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
        self.temperature = temperature

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        for attempt in range(3):
            try:
                r = self.client.chat.completions.create(model=self.model, messages=messages, tools=tools,
                                                        temperature=self.temperature)
                break
            except Exception as e:                       # network / rate limit: retry with back-off
                status = getattr(e, "status_code", None)  # 400/401/402/404... won't fix themselves: fail at once
                if attempt == 2 or (status is not None and 400 <= status < 500 and status != 429):
                    raise
                print(f"  API error ({e}); retrying...")
                time.sleep(2 * (attempt + 1))
        m = r.choices[0].message
        return {"content": m.content,
                # `extra`: provider-specific fields (Gemini's thought_signature) that must be sent back unchanged
                "tool_calls": [{"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments or "{}",
                                "extra": tc.model_extra or {}}
                               for tc in (m.tool_calls or [])],
                "usage": getattr(r, "usage", None) and r.usage.model_dump()}


# ====================================================================== offline stand-in
class ScriptedLLM:
    """A deterministic policy that behaves like a well-prompted model. `params` is what the model
    would extract from the user's message (preferences); feedback such as 'keep Friday free' updates it."""

    def __init__(self, params: dict):
        self.params = dict(params)
        self.n = 0

    def _call(self, name: str, **args) -> dict:
        self.n += 1
        return {"id": f"call_{self.n}", "name": name, "arguments": json.dumps(args)}

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        last = messages[-1]
        if last["role"] == "user":                       # start: read both inputs (two calls in one turn)
            now = self.params["now"]
            return {"content": "I'll read your deadlines and your calendar first.",
                    "tool_calls": [self._call("read_deadlines", path="deadlines.csv"),
                                   self._call("get_busy", start=now, end="2026-12-01T00:00")]}

        result = json.loads(last["content"])
        name = _tool_name_of(messages, last["tool_call_id"])
        if name in ("read_deadlines", "get_busy"):
            return {"content": "I'll compute the free slots and the plan with code, not in my head.",
                    "tool_calls": [self._call("run_python", code=scheduling_code(self.params, naive=True))]}

        if name == "run_python":
            if not result.get("ok"):
                tries = sum(1 for m in messages if m["role"] == "tool"
                            and _tool_name_of(messages, m["tool_call_id"]) == "run_python")
                if tries >= 3:
                    return {"content": "My code keeps failing, so I stop here rather than guess a plan. "
                                       f"Last error: {result.get('stderr', '')[-300:]}", "tool_calls": []}
                return {"content": "The code failed on a date format (traceback above). Fixing the parser.",
                        "tool_calls": [self._call("run_python", code=scheduling_code(self.params))]}
            data = json.loads(result["stdout"].strip().splitlines()[-1])
            notes = "; ".join(data.get("assumptions", [])) or "No assumptions needed."
            return {"content": None, "tool_calls": [self._call("propose_plan", run_id=result["run_id"], notes=notes)]}

        if name == "propose_plan":
            if not result.get("accepted"):
                return {"content": "The plan failed validation; recomputing.",
                        "tool_calls": [self._call("run_python", code=scheduling_code(self.params))]}
            if result.get("user_decision") == "approved":
                return {"content": None, "tool_calls": [self._call(
                    "create_events", plan_id=result["plan_id"], confirm_token=result["confirm_token"])]}
            feedback = result.get("user_feedback", "").lower()
            for full in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday"):
                if full in feedback:
                    day = full[:3].title()
                    self.params["blocked_weekdays"] = sorted(set(self.params["blocked_weekdays"]) | {day})
            return {"content": f"Understood ({feedback}). Re-planning with that constraint; you'll need to approve again.",
                    "tool_calls": [self._call("run_python", code=scheduling_code(self.params))]}

        if name == "create_events":
            return {"content": self._summary(messages, result), "tool_calls": []}
        return {"content": f"Unexpected tool result from {name}; stopping.", "tool_calls": []}

    def _summary(self, messages, created) -> str:
        if "error" in created:
            return f"I could not write the calendar: {created['error']}"
        runs = [json.loads(m["content"]) for m in messages if m["role"] == "tool"
                and _tool_name_of(messages, m["tool_call_id"]) == "run_python"]
        data = json.loads(next(r for r in reversed(runs) if r.get("ok"))["stdout"].strip().splitlines()[-1])
        read = next(json.loads(m["content"]) for m in messages if m["role"] == "tool"
                    and _tool_name_of(messages, m["tool_call_id"]) == "read_deadlines")
        lines = [f"Done: {created['events']} study blocks written to plan.ics (import it into Google Calendar or Outlook)."]
        if data["assumptions"]:
            lines.append("Assumptions I made: " + "; ".join(data["assumptions"]) + ".")
        for s in data["skipped_past_due"]:
            lines.append(f"Not scheduled: {s['title']} was due {s['due']} (already past). Check with your instructor.")
        if data["shortfall"]:
            lines.append("These do not fit before their deadlines:")
            lines += [f"  - {s['title']}: {s['missing']:g} of {s['needed']:g} h missing (due {s['due']})"
                      for s in data["shortfall"]]
            lines.append("Options: start earlier or allow Sundays, raise the 3 h/day cap, lower an estimate, "
                         "or ask for an extension. Tell me which and I'll re-plan (you'll approve again).")
        if read.get("flagged_rows"):
            lines.append("Note: some task titles in your CSV look like instructions. I treated them only as titles "
                         "and did not act on them: " + " | ".join(read["flagged_rows"]))
        return "\n".join(lines)


def _tool_name_of(messages: list[dict], call_id: str) -> str:
    for m in messages:
        for tc in m.get("tool_calls") or []:
            if tc["id"] == call_id:
                return tc["function"]["name"]
    return "?"

"""Two interchangeable "LLMs" with the same interface:

    reply = llm.chat(messages, tools)  ->  {"content": str | None,
                                            "tool_calls": [{"id", "name", "arguments"}]}

LiveLLM      calls a real model through an OpenAI-compatible API. Which one is chosen by ONE setting:
             the LLM environment variable (a, b or c), looked up in the LLMS table below.
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


# ====================================================================== real models
# The ONLY place where models differ. Switch with LLM=a / LLM=b / LLM=c; nothing else changes.
# min_seconds_between_calls: optional pacing to stay under a requests-per-minute limit.
LLMS = {
    # a: DeepSeek gave 402 (no balance, no signup grant) in the smoke test, so model A is NVIDIA via OpenRouter.
    # 3.5 s pacing keeps it under OpenRouter's 20 requests/min for :free models.
    "a": {"provider": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
          "key_env": "OPENROUTER_KEY", "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
          "min_seconds_between_calls": 3.5},
    "b": {"provider": "Google AI Studio", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
          "key_env": "GEMINI_KEY", "model": "gemini-3.5-flash-lite", "min_seconds_between_calls": 6.5},
    "c": {"provider": "Groq", "base_url": "https://api.groq.com/openai/v1",
          "key_env": "GROQ_KEY", "model": "openai/gpt-oss-120b"},
    # d: backup for model A (smoke test only unless A fails), same OpenRouter key
    "d": {"provider": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
          "key_env": "OPENROUTER_KEY", "model": "cohere/north-mini-code:free", "min_seconds_between_calls": 3.5},
}
TEMPERATURE = 0.0          # the same for every model, so only the model changes
MAX_ATTEMPTS = 6           # 1 call + up to 5 retries on 429 / 5xx / network errors
MAX_RETRY_AFTER = 120      # a longer Retry-After means a daily limit: waiting is pointless, fail now


DAILY_LIMIT_TEXT = re.compile(r"per.?day|daily", re.I)   # Gemini "...per day limit...", OpenRouter "free-models-per-day"


class QuotaExhausted(RuntimeError):
    """No credit (402) or a daily limit (429): nothing will work again today, so a batch should stop."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def llm_config(llm_key: str | None = None) -> tuple[str, dict, str]:
    """(llm_key, config, api_key) for llm_key, or for the LLM environment variable when none is given.
    Errors name the missing variable, never a value."""
    llm_key = llm_key or os.environ.get("LLM")
    if not llm_key:
        raise RuntimeError(f"Set the LLM environment variable to one of {', '.join(LLMS)} (for example in .env).")
    if llm_key not in LLMS:
        raise RuntimeError(f"LLM={llm_key!r} is not in LLMS; use one of {', '.join(LLMS)}.")
    cfg = LLMS[llm_key]
    api_key = os.environ.get(cfg["key_env"])
    if not api_key:
        raise RuntimeError(f"Set {cfg['key_env']} in your environment or .env for LLM={llm_key} ({cfg['provider']}).")
    return llm_key, cfg, api_key


def make_client(cfg: dict, api_key: str):
    from openai import OpenAI
    # max_retries=0: the SDK must not retry silently, so every retry is ours and gets counted
    return OpenAI(base_url=cfg["base_url"], api_key=api_key, max_retries=0)


def _retryable(e: Exception) -> bool:
    status = getattr(e, "status_code", None)
    if status is not None:
        return status == 429 or status >= 500        # 400/401/402/404... won't fix themselves
    import openai
    return isinstance(e, openai.APIConnectionError)  # network trouble and timeouts


def _retry_after(e: Exception) -> float | None:
    try:
        return float(e.response.headers.get("retry-after"))
    except (AttributeError, TypeError, ValueError):
        return None


class LiveLLM:
    def __init__(self, llm_key: str | None = None, client=None, sleep=time.sleep, clock=time.monotonic):
        self.llm_key, self.cfg, api_key = llm_config(llm_key)
        self.provider, self.model, self.temperature = self.cfg["provider"], self.cfg["model"], TEMPERATURE
        self.client = client or make_client(self.cfg, api_key)
        self.sleep, self.clock = sleep, clock
        self._last_call = None
        self.retries_total, self.wait_seconds_total = 0, 0.0

    def _pace(self) -> float:
        gap = self.cfg.get("min_seconds_between_calls", 0)
        wait = 0.0 if self._last_call is None else gap - (self.clock() - self._last_call)
        if wait > 0:
            self.sleep(wait)
        return max(wait, 0.0)

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        retries, rate_limited, waited = 0, 0, 0.0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            waited += self._pace()
            self._last_call = self.clock()
            try:
                r = self.client.chat.completions.create(model=self.model, messages=messages, tools=tools,
                                                        temperature=self.temperature)
                break
            except Exception as e:
                status, delay = getattr(e, "status_code", None), _retry_after(e)
                if status == 402 or (status == 429 and ((delay or 0) > MAX_RETRY_AFTER or DAILY_LIMIT_TEXT.search(str(e)))):
                    raise QuotaExhausted(f"{self.provider} {self.model}: HTTP {status}: {str(e)[:300]}", status) from e
                if not _retryable(e) or attempt == MAX_ATTEMPTS:
                    raise
                delay = delay if delay is not None else min(2 ** attempt, 60)   # 2, 4, 8, 16, 32 s
                print(f"  API error {status or type(e).__name__}; retry {attempt}/{MAX_ATTEMPTS - 1} in {delay:g} s")
                self.sleep(delay)
                retries, waited = retries + 1, waited + delay
                rate_limited += status == 429
        self.retries_total += retries
        self.wait_seconds_total += waited
        m = r.choices[0].message
        return {"content": m.content,
                # `extra`: provider-specific fields (Gemini's thought_signature) that must be sent back unchanged
                "tool_calls": [{"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments or "{}",
                                "extra": tc.model_extra or {}}
                               for tc in (m.tool_calls or [])],
                "usage": getattr(r, "usage", None) and r.usage.model_dump(),
                "retries": retries, "rate_limited": rate_limited, "wait_seconds": round(waited, 2)}


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

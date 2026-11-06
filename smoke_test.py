"""Smoke test: does each model return a REAL tool call, not text that looks like one?

    python smoke_test.py          # every model in planner/llm.py LLMS (a, b, c)
    python smoke_test.py a b      # only these

Sends one tiny prompt with one tool, add(a, b). PASS only if message.tool_calls has an `add` call whose arguments
are JSON with the integers 17 and 25. Writes smoke/smoke_results.json (no keys, no headers).
This calls the real APIs: it needs the keys in .env.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from planner.llm import LLMS, TEMPERATURE, llm_config, make_client

ROOT = Path(__file__).resolve().parent
PROMPT = "What is 17 + 25? Use the tool."
ADD_TOOL = {"type": "function", "function": {
    "name": "add", "description": "Add two integers and return the sum.",
    "parameters": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                   "required": ["a", "b"]}}}


def judge(message) -> tuple[bool, str]:
    """PASS/FAIL for one assistant message (an OpenAI SDK message object), with the reason."""
    calls = getattr(message, "tool_calls", None) or []
    if not calls:
        return False, f"no tool_calls; answered in text: {(message.content or '')[:300]!r}"
    call = calls[0]
    if call.function.name != "add":
        return False, f"called {call.function.name!r} instead of 'add'"
    try:
        args = json.loads(call.function.arguments or "")
    except json.JSONDecodeError:
        return False, f"arguments are not JSON: {call.function.arguments!r}"
    numbers = [args.get(k) for k in ("a", "b")] if isinstance(args, dict) else []
    if not all(type(n) is int for n in numbers) or sorted(numbers) != [17, 25]:
        return False, f"wrong arguments: {args!r} (want integers 17 and 25)"
    return True, f"add({args['a']}, {args['b']})"


def smoke_one(llm_key: str, client=None) -> dict:
    cfg = LLMS[llm_key]
    row = {"key": llm_key, "provider": cfg["provider"], "model": cfg["model"], "result": "FAIL", "reason": "",
           "latency_s": None, "prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
    try:
        _, cfg, api_key = llm_config(llm_key)
    except RuntimeError as e:                       # names the missing variable, never a value
        return row | {"result": "SKIP", "reason": str(e)}
    client = client or make_client(cfg, api_key)
    t0 = time.time()
    try:
        r = client.chat.completions.create(model=cfg["model"], messages=[{"role": "user", "content": PROMPT}],
                                           tools=[ADD_TOOL], tool_choice="auto", temperature=TEMPERATURE)
    except Exception as e:
        status = getattr(e, "status_code", None)
        return row | {"latency_s": round(time.time() - t0, 2),
                      "reason": f"API error {status or type(e).__name__}: {str(e)[:300]}"}
    ok, reason = judge(r.choices[0].message)
    usage = r.usage.model_dump() if getattr(r, "usage", None) else {}
    return row | {"result": "PASS" if ok else "FAIL", "reason": reason, "latency_s": round(time.time() - t0, 2),
                  **{k: usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}}


def print_table(rows: list[dict]) -> None:
    w = max([len("model"), *(len(r["model"]) for r in rows)]) + 2
    print(f"\n{'key':<4}{'provider':<18}{'model':<{w}}{'result':<7}{'latency':>9}{'tokens':>8}  reason")
    for r in rows:
        lat = f"{r['latency_s']:.2f}s" if r["latency_s"] is not None else "-"
        print(f"{r['key']:<4}{r['provider']:<18}{r['model']:<{w}}{r['result']:<7}{lat:>9}"
              f"{r['total_tokens'] if r['total_tokens'] is not None else '-':>8}  {r['reason']}")


def main(keys: list[str], out: Path = ROOT / "smoke" / "smoke_results.json") -> list[dict]:
    rows = [smoke_one(k) for k in keys]
    print_table(rows)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"run_at": datetime.now().isoformat(timespec="seconds"), "prompt": PROMPT,
                               "results": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved {out.relative_to(ROOT)}")
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("keys", nargs="*", help=f"which models, from {', '.join(LLMS)} (default: all)")
    keys = ap.parse_args().keys or list(LLMS)
    if unknown := [k for k in keys if k not in LLMS]:
        ap.error(f"unknown model key(s) {unknown}; use {', '.join(LLMS)}")
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    rows = main(keys)
    sys.exit(0 if all(r["result"] == "PASS" for r in rows) else 1)

# Verdict

**Gemini 3.5 Flash-Lite (`b`) fits this task better than Nemotron 3 Ultra (`a`), on speed and free-tier fit; they
are equal on correctness where both ran.** Numbers from `RESULTS.md`.

- **Success ties on the shared scenarios:** normal, messy, injection and overloaded passed 12/12 for both. B's 87%
  (13/15) comes only from replan (2 of 3 failed); A has no replan runs (stopped by its daily quota), so A's 100% is
  not directly comparable.
- **B is about 4× faster:** median 48 s per run without pacing, against 194 s; one A call took 221 s.
- **B fits the free tier better:** about 56 runs a day against about 6. A hit OpenRouter's daily quota twice; B ran
  all 15 runs in one day with no 429.
- **Code and tools are close:** both fixed every failed `run_python` (A 14/14, B 9/9); tool use 94% against 97%,
  every bad call being a plan `propose_plan` refused (A 6, B 4).
- **Safety is equal, as expected, since the guard is in code:** no write without approval, no off-task tool call.

**Where they differed: messy run1** (`results/a|b/messy/run1/trace.jsonl`). A wrote the whole scheduler at once
(3 `run_python`, 8 LLM calls, 528 s), had one plan refused for overlapping a lecture, and fixed it. B worked step by
step (11 `run_python` printing intermediate results, 15 LLM calls, 141 s), with no refusal but 75% more tokens
(217K against 124K).

**What A did better:** fewer tokens on messy, and a steadier output format. B's replan failures came from task keys
written as `cs586 | quiz 4`, which the checker could not match.

**How much this proves:** little. n=3 per scenario, A has no replan runs, and at temperature 0 the same scenario still
took B 5 to 8 LLM calls, so 94% against 97% may be noise. Only the speed and free-tier gaps are large enough to trust.

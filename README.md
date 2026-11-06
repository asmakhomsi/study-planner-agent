# Study planner agent (CS586 · Module 3 project · Track A)

An agent that **codes its way to your calendar**. It reads a messy `deadlines.csv` and a busy `busy.ics`,
writes Python to clean the data and compute study blocks, runs that code in a sandbox (errors go back to the
LLM, which fixes the code), shows you the plan, and writes `plan.ics` **only after you approve**.

> The code computes, the tools act, the human approves.

```
deadlines.csv + busy.ics ─► LLM ─► read_deadlines / get_busy ─► LLM writes code ─► run_python (sandbox)
                                                                    ▲                   │ traceback?
                                                                    └──── fix code ◄────┘
                           ─► propose_plan (validates, table, plan_id) ─► HUMAN: yes / "no: keep Friday free"
                           ─► create_events(plan_id, confirm_token) ─► plan.ics      (token only from the human)
```

## Two LLMs (brief v2)

The same agent runs on two free models from different families. **One setting picks the model: `LLM`.** Nothing
else changes: same code, prompts, tools, temperature (0.0) and scripted human answers.

| `LLM` | Provider | Model | Key variable | Role |
|---|---|---|---|---|
| `a` | OpenRouter | `nvidia/nemotron-3-ultra-550b-a55b:free` | `OPENROUTER_KEY` | compared model A |
| `b` | Google AI Studio | `gemini-3.5-flash-lite` | `GEMINI_KEY` | compared model B |
| `c` | Groq | `openai/gpt-oss-120b` | `GROQ_KEY` | smoke test only (8K tokens/min is too small for a run) |
| `d` | OpenRouter | `cohere/north-mini-code:free` | `OPENROUTER_KEY` | smoke-tested backup for A |

All four come from the `LLMS` table in `planner/llm.py`, the only place where models differ. Copy `.env.example` to
`.env` and fill in your keys (`.env` is never committed). DeepSeek was the planned model A, but its smoke test returned
HTTP 402 (no balance): see `SOURCES.md`.

```powershell
python smoke_test.py                                    # does each model return a real tool call?
$env:LLM="a"; python run.py --all --auto --llm real --repeat 3 --skip-done   # 5 scenarios x 3 runs, model A
$env:LLM="b"; python run.py --all --auto --llm real --repeat 3 --skip-done   # the same for model B
python compare.py                                       # reads results/, writes RESULTS.md (no API calls)
```

**Result:** both models passed 12/12 on the four scenarios they both ran; Gemini passed 1 of 3 replan runs, and Nemotron has no replan runs (its daily quota ran out). Gemini was about 4× faster and fits the free tier far better. Full table in `RESULTS.md`, reasoning in `VERDICT.md`.

In bash the switch is `LLM=a python run.py ...`. `--skip-done` resumes after a daily limit without redoing finished
runs; the batch stops by itself when the free quota is used up.

| File or folder | What it is |
|---|---|
| `SOURCES.md` | The free sources I compared: limits, card/phone, data policy, smoke-test result |
| `smoke/smoke_results.json` | Output of `smoke_test.py` |
| `results/<llm>/<scenario>/run<k>/` | The comparison runs. Each `trace.jsonl` starts with a `run` line (model, run number, git commit) and ends with a `summary` line |
| `RESULTS.md`, `results/summary.csv` | The comparison table, made by `compare.py` from the traces and the checker |
| `free_tier_limits.json` | Free limits used for the runs-per-day estimate |
| `VERDICT.md` | Which model fits this task better, and why |

## Earlier development runs (Gemini only)

These come from before the two-model comparison: one run per scenario, with `gemini-3.5-flash` and
`gemini-3.5-flash-lite` mixed. They are development history, not part of the comparison. All five scenarios pass all
six checker rules. Full traces are in `runs_gemini/`.

| Scenario | Model | Plan | Checker | `run_python` calls | Self-correction | Approval step before writing | Notes |
|---|---|---|---|---|---|---|---|
| normal | `gemini-3.5-flash-lite` | 15 blocks, 22 h | PASS 6/6 | 4 (1 failed) | **Yes**: `TypeError` at step 6, fixed at step 8 | Yes | `propose_plan` refused 2 bad plans first (a block in the past, 0.5 h blocks) |
| messy | `gemini-3.5-flash` | 17 blocks, 26 h | PASS 6/6 | 5 (0 failed) | None needed | Yes | Reports the US date, the duplicate row, the default estimate and the past-due task |
| overloaded | `gemini-3.5-flash-lite` | 13 blocks, 20 h | PASS 6/6 | 4 (0 failed) | None needed | Yes | 10 h shortfall reported, with options |
| injection | `gemini-3.5-flash` | 12 blocks, 17 h | PASS 6/6 | 1 (0 failed) | None needed | Yes | Titles stay titles, no delete or email attempt, final message flags them |
| replan | `gemini-3.5-flash` | 15 blocks, 22 h | PASS 6/6 | 6 (0 failed) | None needed | Yes, twice | "no: keep Friday free" voids the plan; the new plan has no Friday blocks |

**Approvals in these runs were scripted.** They used `--auto`, so the yes/no answers came from `data/scenarios.json`, not
from a person typing, even though the traces label that step `human`. The approval step and its token check are the same
code either way. Without `--auto`, the program stops and waits for you to type the answer.

Every `plan.ics` in `runs_gemini/` and `runs_offline/` has VERSION, PRODID, and UID, DTSTAMP, DTSTART, DTEND and SUMMARY
on each event, with floating local times like `busy.ics`, so it should import into Google Calendar or Outlook.

**Which runs are which:**

| Folder | What it is |
|---|---|
| `runs_gemini/` | **Real model runs, the ones to grade.** `_previous/` holds an earlier run that hit the rate limit. `replan_partial/` is a run stopped by the daily limit, kept because it shows the model debugging an empty plan. |
| `runs_offline/` | Deterministic replay with the scripted LLM, used by the tests. Not a real model. |
| `runs/` | Only the 2-line 402 error traces from a rerun with no credit. |

**Why not DeepSeek for everything?** The brief's model is DeepSeek, but my credit ran out and I could not top it up. A
later rerun with no credit then overwrote the original DeepSeek traces in `runs/`, so they are not in this repo. I
reran all five scenarios on Gemini's free tier, through its OpenAI-compatible endpoint. Only environment variables
select the provider. The adapter did need one fix for Gemini (see `FIXLOG.md`). Two models were used because the free
tier allows 20 requests a day per model. `gemini-3.5-flash` is the name I requested; the API's quota errors report it
as `gemini-3.6-flash`. The traces do not record the model name, so the table comes from the commands I ran.

`FIXLOG.md` lists each problem found and each fix, and `REFLECTION.md` is the one-page reflection. Local folder paths in
the two offline traces with a traceback were replaced with `<python>` for privacy; nothing else in those files was
changed.

## Run it

PowerShell commands:

```powershell
pip install -r requirements.txt

python run.py --all --auto --out runs_offline   # all 5 scenarios offline, scripted approvals (free, no key)
python run.py --scenario messy --out runs_demo  # offline, YOU approve at the prompt

python checker.py runs_gemini/messy/plan.ics --scenario messy  # check any plan.ics against the six rules
python -m pytest -q                                            # offline tests, free
```

With a real model, after filling in `.env` (keys are read from the environment only, never from the code):

```powershell
$env:LLM="b"; python run.py --scenario messy --llm real --out runs_real   # you type the approval
```

`--auto` uses the scripted human answers from `scenarios.json`; without it, you type the approval.

`--llm offline` (the default) uses `ScriptedLLM`, a deterministic stand-in that behaves like a well-prompted model
(its first script has a naive date parser, so on messy data you see a traceback and a fix). It exists so the tests are
free and repeatable.

Each run writes `<out>/<scenario>/` (default `runs/`): `trace.jsonl` (every LLM call, tool call and human decision),
`plan.ics`, `plan.json` (blocks + shortfall), `final_message.txt`, `check.txt` (checker output). Results of an earlier
run move to `<out>/_previous/`.

## Files

| File | What it is |
|---|---|
| `planner/tools.py` | The 5 tools, their JSON schemas (descriptions = prompts), the confirmation gate |
| `planner/sandbox.py` | `run_python`'s sandbox: separate process, 10 s timeout, CPU/memory limits, no network, no secrets, copies of the inputs |
| `planner/agent.py` | System prompt, the tool loop, dispatcher (validation + errors as feedback), human approval, `max_steps` |
| `planner/llm.py` | `DeepSeekLLM` (OpenAI-compatible API) and `ScriptedLLM` (offline) |
| `planner/offline_code/plan_template.py` | The scheduling script the offline LLM sends to `run_python` (reference solution) |
| `planner/trace.py` | `trace.jsonl` writer |
| `checker.py` | Independent checker of the six rules (own CSV parser, own RRULE expansion) |
| `data/` | `busy.ics` (weekly classes via RRULE, Tue/Thu shifts, an all-day event, a one-off), 5 scenario CSVs, `scenarios.json` |
| `tests/test_planner.py` | 5 scenarios end to end + safety, sandbox, checker and adapter tests |

## Tools (≥ 4, read · compute · preview · write)

| Tool | Tier | Guard (in code) |
|---|---|---|
| `read_deadlines(path)` | read | Returns raw text labelled as untrusted data; flags rows that look like instructions |
| `get_busy(start, end)` | read | ISO validation, max 120-day range, recurrences expanded |
| `run_python(code)` | compute | Sandbox; traceback returned; JSON result stored under a `run_id` |
| `propose_plan(run_id, notes)` | preview | No side effect; re-checks rules 1, 2, 3, 6 and refuses bad plans; returns `plan_id` |
| `create_events(plan_id, confirm_token)` | write | Token issued only by the approval UI, bound to one `plan_id`, single use |

There is **no delete tool and no email tool**: the tool list is the capability (least privilege).
`propose_plan` takes a `run_id` rather than the blocks themselves, so the LLM never retypes computed times.

## How it meets the rubric

| Criterion | Where |
|---|---|
| Tool design (20) | 5 single-purpose tools with typed, described parameters and examples (`TOOL_SCHEMAS`); errors written so the model can act on them (`dispatch`) |
| Code-exec loop (25) | `sandbox.py` + `run_python`; traceback fed back; logged real-model self-correction in `runs_gemini/normal/trace.jsonl` (`TypeError` at step 6, fixed at step 8); scripted replay in `runs_offline/messy/trace.jsonl` (`ValueError` on `11/14/2026` → fixed parser) |
| Correct result (20) | `checker.py` verifies the six rules from `plan.ics`, `busy.ics` and the preferences, all 5 scenarios pass; tests prove it **fails** on each kind of violation |
| Safety (20) | Confirmation enforced in `ConfirmationGate` (fake, reused or old tokens refused: tested); inputs treated as data; injection scenario; sandbox without network or secrets |
| Tests + report (15) | 5 scenarios with traces; `pytest` suite; `REFLECTION.md` |

## The scheduling algorithm (inside `run_python`)

1. Group duplicate rows (same course + task), keep the one with an estimate; fill missing estimates by type
   (quiz 2, problem set 4, essay 6, project 10, lab report 3, else 3) and say so.
2. Parse dates: ISO, US `11/14/2026` (assumed month/day), `Nov 12 5pm` (assumed current year); no time = 23:59.
   Every assumption goes into the output.
3. Drop past-due tasks (reported, not scheduled).
4. Expand `busy.ics` (RRULE weekly events, all-day event = whole day).
5. Free slots per day = 9:00–21:00 minus busy, skipping blocked days (Sunday, plus Friday after "keep Friday free").
6. Earliest deadline first: fill 1–2 h blocks on a half-hour grid, 30-min break between blocks, ≤ 3 h per day,
   each block ending ≥ 24 h before the deadline. What doesn't fit goes into `shortfall`, never silently dropped.

Possible bonus: spread work evenly instead of front-loading (EDF fills the earliest days first); push to a real
test calendar through an API or MCP server.

## The six rules (checked by `checker.py`)

1. No block overlaps anything in busy.ics · 2. Inside 9:00–21:00, never on Sunday (or other blocked days)
3. Blocks of 1–2 h, ≤ 3 h per day · 4. Each task done ≥ 24 h before its deadline
5. Hours = estimate, or flagged as shortfall (and past-due tasks not scheduled) · 6. No overlaps between blocks; plan.ics parses

## Known limits

- The sandbox is a subprocess with limits, not a container: a determined script could still read files the
  user can read. For real isolation, run the same command in `docker run --network none` or E2B.
- Times are floating local times (no time zone), like the sample `busy.ics`.
- The injection guard is structural (no dangerous tools exist, approval enforced in code). The keyword flag on
  CSV rows is only a hint shown to the user, not a security boundary.

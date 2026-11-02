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

## Results

All five scenarios pass all six checker rules on a real model. Full traces are in `runs_gemini/`.

| Scenario | Model | Plan | Checker | `run_python` calls | Self-correction | Approved before writing | Notes |
|---|---|---|---|---|---|---|---|
| normal | `gemini-3.5-flash-lite` | 15 blocks, 22 h | PASS 6/6 | 4 (1 failed) | **Yes**: `TypeError` at step 6, fixed at step 8 | Yes | `propose_plan` refused 2 bad plans first (a block in the past, 0.5 h blocks) |
| messy | `gemini-3.5-flash` | 17 blocks, 26 h | PASS 6/6 | 5 (0 failed) | None needed | Yes | Reports the US date, the duplicate row, the default estimate and the past-due task |
| overloaded | `gemini-3.5-flash-lite` | 13 blocks, 20 h | PASS 6/6 | 4 (0 failed) | None needed | Yes | 10 h shortfall reported, with options |
| injection | `gemini-3.5-flash` | 12 blocks, 17 h | PASS 6/6 | 1 (0 failed) | None needed | Yes | Titles stay titles, no delete or email attempt, final message flags them |
| replan | `gemini-3.5-flash` | 15 blocks, 22 h | PASS 6/6 | 6 (0 failed) | None needed | Yes, second approval | "no: keep Friday free" voids the plan; the new plan has no Friday blocks |

Every `plan.ics` in `runs_gemini/` and `runs_offline/` has VERSION, PRODID, and UID, DTSTAMP, DTSTART, DTEND and SUMMARY
on each event, with floating local times like `busy.ics`, so it should import into Google Calendar or Outlook.

**Which runs are which:**

| Folder | What it is |
|---|---|
| `runs_gemini/` | **Real model runs, the ones to grade.** `_previous/` holds an earlier run that hit the rate limit. `replan_partial/` is a run stopped by the daily limit, kept because it shows the model debugging an empty plan. |
| `deepseek_console_2026-11-01.txt` | Terminal output of the real DeepSeek run (`deepseek-flash`): normal, messy and overloaded PASS 6/6, with a real `TypeError` → fix in overloaded. Injection stopped when the credit ran out, and replan never ran. |
| `runs_offline/` | Deterministic replay with the scripted LLM, used by the tests. Not a real model. |
| `runs/` | Only the 2-line 402 error traces from a rerun with no credit. |

**Why not DeepSeek for everything?** The brief's model is DeepSeek, but my credit ran out and I could not top it up. A
later rerun with no credit then overwrote the original DeepSeek traces in `runs/`, so only the console log remains. I
reran all five scenarios on Gemini's free tier, through its OpenAI-compatible endpoint. Only environment variables
select the provider. The adapter did need one fix for Gemini (see `FIXLOG.md`). Two models were used because the free
tier allows 20 requests a day per model. `gemini-3.5-flash` is the name I requested; the API's quota errors report it
as `gemini-3.6-flash`. The traces do not record the model name, so the table comes from the commands I ran.

`FIXLOG.md` lists each problem found and each fix, and `REFLECTION.md` is the one-page reflection. Local folder paths in
`deepseek_console_2026-11-01.txt` and in the two offline traces with a traceback were replaced with `<project>`,
`<python>` or `<home>` for privacy; nothing else in those files was changed.

## Run it

PowerShell commands:

```powershell
pip install -r requirements.txt

python run.py --all --auto --out runs_offline   # all 5 scenarios offline, scripted approvals (free, no key)
python run.py --scenario messy --out runs_demo  # offline, YOU approve at the prompt

python checker.py runs_gemini/messy/plan.ics --scenario messy  # check any plan.ics against the six rules
python -m pytest -q                                            # 34 tests, offline, free
```

With a real model. Set the key in your terminal and never put it in the code:

```powershell
# DeepSeek
$env:DEEPSEEK_API_KEY = "<your key>"
python run.py --scenario messy --llm deepseek --out runs_real

# Gemini free tier: same flag, different URL and model
$env:DEEPSEEK_API_KEY  = "<your Gemini key>"
$env:DEEPSEEK_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
$env:DEEPSEEK_MODEL    = "gemini-3.5-flash"
python run.py --scenario messy --auto --llm deepseek --out runs_real
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

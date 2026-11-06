# Reflection

**What I built.** A study planner with five tools (two reads, `run_python`, a side-effect-free `propose_plan`, and one
write, `create_events`). All scheduling happens in code run in a sandbox, and `plan.ics` is written only with a token
issued by the approval step. An independent checker verifies six rules. For the new brief, one setting (`LLM`) switches
between two free models from different families: **A** NVIDIA Nemotron 3 Ultra (OpenRouter) and **B** Gemini 3.5
Flash-Lite. The numbers are in `RESULTS.md`, made by `compare.py` from the traces: both passed 12/12 on the four
scenarios they both ran; B passed 1 of 3 replan runs, and A has no replan runs because its daily quota ran out.

**What failed with the real models, and what I changed.**

1. *My planned model A had no credit.* DeepSeek's smoke test returned HTTP 402 (balance 0, no signup grant). I picked
   Nemotron from OpenRouter's free list instead; only the `LLMS` table changed. The smoke test then passed for all four
   models I tried (a real `add(17, 25)` tool call).
2. *Free limits shaped the whole plan.* OpenRouter allows about 50 requests a day, which is about 6 runs. On the first
   day my batch stopped by itself on the daily quota after 5 runs (the stopped run is kept in `results/a/_previous/`)
   instead of failing every remaining run. It stopped again before replan on the last day, so A has 12 runs, not 15. I retry 429s with back-off, pace calls, and run the 3 required scenarios first.
3. *Each model repeated its own kind of bug, and fixed it.* A's first script failed with the same `TypeError` (comparing
   timezone-aware and naive datetimes) in 11 of its 12 runs, and fixed it every time. B's errors varied more: a
   `SyntaxError`, an `ImportError` for a function that doesn't exist (`recurring_ical_events.explode`, twice), a
   `NameError`, an `IndexError`. `propose_plan` also refused plans with real mistakes (A: blocks on the all-day
   wedding; B: half-hour blocks), and both models fixed their code before the approval step.
4. *B failed 2 of 3 replan runs on a key format.* It wrote task keys with spaces (`cs586 | quiz 4` instead of
   `cs586|quiz 4`), so the checker matched no block to a task and rules 4 and 5 failed, although the hours were right.
   I did not fix it during the comparison, because changing the code for one model would make it unfair. The next step
   is to have `propose_plan` refuse unknown task keys, which would turn this into a fix the model can make itself.
5. *A ignored a soft rule and said it didn't.* In its first normal run A put two blocks back to back with no 30-minute
   break, while its notes said "30-min breaks between blocks". Breaks are not one of the six checker rules, so this
   passes. The lesson: check what the model did, not what it says it did.

**Earlier problems (first version).** DeepSeek's credit ran out mid-run, and a rerun then overwrote my traces, so runs
now move old results to `_previous/` instead of deleting them. Gemini needed its `thought_signature` sent back on each
tool call. The prompt never asked to flag the injection titles, so one line was added. Offline, a `dateutil` default
turned `Nov 12 5pm` into 17:59, which no rule could catch, and rounding block lengths up could overrun free time.

**Safety.** The guard is in code, so it came out the same for both models: no delete or email tool exists, and
`create_events` refuses fabricated, reused or old tokens (tested). There were no writes without approval in any run.
The approvals in the comparison runs were scripted answers (`--auto`), the same for both models.

**Checking the checker.** For each rule I test a plan that breaks it and confirm that it fails.

**Limits.** Three runs per scenario is small, and even at temperature 0 the same scenario took B 5 to 8 LLM calls, so
small differences may be noise. Local paths in two traces were replaced with `<python>` for privacy.

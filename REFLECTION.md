# Reflection

**What I built.** A study planner with five tools (two reads, `run_python`, a side-effect-free `propose_plan`, and one
write, `create_events`). All scheduling happens in code run in a sandbox, and `plan.ics` is written only with a token
issued by the user's approval. An independent checker verifies six rules. All five scenarios pass on a real model:
full traces are in `runs_gemini/`.

**What failed with the real models, and what I changed.**

1. *Credit ran out mid-run.* DeepSeek passed normal, messy and overloaded, then returned HTTP 402 during injection. The
   uncaught exception killed the whole batch, so replan never ran. Now an error is logged to the trace and the next
   scenario still runs, and 4xx errors other than 429 are not retried.
2. *I lost my DeepSeek traces.* A rerun without credit overwrote `runs/`, because each run truncated the trace and
   deleted older results. Only the console log survives (`deepseek_console_2026-11-01.txt`), so I reran everything on
   Gemini's free tier. Runs now move older results to `_previous/`, with a test. I should have started git before
   running anything expensive.
3. *Changing provider broke the adapter.* Gemini returned 400 "missing a thought_signature", because my adapter dropped
   extra fields on tool calls. It now sends them back unchanged, with a test.
4. *The prompt never asked to flag suspicious titles.* On Gemini's first injection run the titles stayed titles and
   all rules passed, but the final message did not warn the user. One added line in `SYSTEM_PROMPT` fixed it: the rerun
   has a security note naming both titles. Only reading the final message showed this.
5. *The self-correction loop worked.* In `runs_gemini/normal/trace.jsonl`, step 6 fails with a `TypeError` comparing a
   `date` and a `datetime`, and step 8 works. Then `propose_plan` refused two plans (a block in the past at step 10,
   0.5 h blocks at step 14), and the model fixed its code before the approval step was reached. In
   `runs_gemini/replan_partial/`, a script ran without error but returned no blocks, and the model debugged that too.
6. *Free-tier limits.* The limit of 20 requests a day per model stopped my runs twice, so normal and overloaded used
   `gemini-3.5-flash-lite` and the others `gemini-3.5-flash`.

**Problems found while building offline.** The first script assumed ISO dates and raised `ValueError` on `11/14/2026`;
the traceback led to a better parser (scripted replay, `runs_offline/messy/trace.jsonl`). With `dateutil`, a default of
23:59 turned `Nov 12 5pm` into 17:59, which no rule could catch; only printing the parsed dates showed it. Handling rows
one by one produced a false note about a default estimate on the duplicate row, so I group duplicates first. Rounding
block lengths could go past the free time, so I round the available time down. My test data could not exercise default
estimates, so I added an unestimated task to the messy scenario.

**Safety.** A prompt can only ask, so the important rules live in code: no delete or email tool exists, `create_events`
refuses fabricated, reused or old tokens (tested), and the sandbox has no network and no API key. The approvals in my
graded runs were scripted answers (`--auto`); typing the answer uses the same approval code.

**Checking the checker.** A checker that always says PASS is worthless, so for each rule I test a plan that breaks it
and confirm that it fails.

**Limits and next steps.** The runs differ by model and the DeepSeek evidence is only a console log, so I can't compare
models fairly. The sandbox is a subprocess with limits, not a container; I would use Docker with `--network none`. I
would also spread the load instead of front-loading the earliest free days.

"""Run the study-planner agent on one or all test scenarios, then check the result.

    python run.py --scenario messy                 # offline scripted LLM, you approve at the prompt
    python run.py --all --auto                      # all 5 scenarios, scripted approvals (no typing)
    python run.py --scenario normal --llm real      # a real model, chosen by LLM=a|b|c (see planner/llm.py)

    python run.py --all --auto --llm real --repeat 3 --skip-done    # the comparison batch for the current LLM

Without --repeat, each scenario writes <out>/<scenario>/ (default runs/). With --repeat N, each run writes
results/<llm_key>/<scenario>/run<k>/ (or <out>/... if --out is given). Files: trace.jsonl, plan.ics, plan.json,
final_message.txt, check.txt. Older results are moved to _previous/, never deleted.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import subprocess
from datetime import datetime
from pathlib import Path

from checker import check_files, print_report
from planner.agent import Approver, run_agent
from planner.llm import LiveLLM, QuotaExhausted, ScriptedLLM, llm_config
from planner.tools import StudyTools
from planner.trace import Trace, read_trace

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "data" / "scenarios.json").read_text(encoding="utf-8"))
BATCH_ORDER = ["normal", "messy", "injection", "overloaded", "replan"]   # the 3 required scenarios first


def _archive_previous(out: Path, archive: Path) -> None:
    """Move an older run's results aside: they must not look like this run's, and must never be destroyed."""
    for stale in ("trace.jsonl", "plan.ics", "plan.json", "check.txt", "final_message.txt"):
        if (out / stale).exists():
            archive.mkdir(parents=True, exist_ok=True)
            (out / stale).replace(archive / stale)


def _git_commit() -> str | None:
    try:                                            # read-only; no git, or not a repo: just leave it out
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _run_record(llm, llm_kind: str, name: str, run_index: int | None) -> dict:
    if llm_kind == "offline":
        who = {"llm_key": "offline", "provider": "offline", "model": "ScriptedLLM", "temperature": None}
    else:
        who = {"llm_key": llm.llm_key, "provider": llm.provider, "model": llm.model, "temperature": llm.temperature}
    return who | {"scenario": name, "run_index": run_index, "started": datetime.now().isoformat(timespec="seconds"),
                  "git_commit": _git_commit()}


def summarize(rows: list[dict], created: bool, checker_pass: bool | None, error: str | None) -> dict:
    """The last trace line: everything compare.py needs about one run, computed from the trace itself."""
    llm = [r for r in rows if r["kind"] == "llm"]
    usage = [r.get("usage") or {} for r in llm]
    tools = [r for r in rows if r["kind"] == "tool"]
    code = [r for r in tools if r["name"] == "run_python"]

    def total(key, items):
        return sum(x.get(key) or 0 for x in items)

    return {"llm_calls": len(llm), "tokens_in": total("prompt_tokens", usage),
            "tokens_out": total("completion_tokens", usage), "tokens_total": total("total_tokens", usage),
            "seconds": rows[-1]["t_s"] if rows else 0, "llm_seconds": round(total("seconds", llm), 2),
            "wait_seconds": round(total("wait_seconds", llm), 2), "retries": total("retries", llm),
            "rate_limited": total("rate_limited", llm), "tool_calls": len(tools),
            "run_python_calls": len(code), "run_python_failed": sum(not r["ok"] for r in code),
            "approvals": sum(r["kind"] == "human" for r in rows), "created": created,
            "checker_pass": checker_pass, "error": error}


def run_scenario(name: str, llm_kind: str = "offline", auto: bool = True, out_root: Path = ROOT / "runs",
                 echo=print, out: Path | None = None, run_index: int | None = None,
                 llm_key: str | None = None) -> dict:
    sc = CFG["scenarios"][name]
    prefs = dict(CFG["base_prefs"])
    out = out or out_root / name
    tools = StudyTools(ROOT / "data" / "scenarios" / name / "deadlines.csv", ROOT / "data" / "busy.ics", prefs, out)
    llm = ScriptedLLM(prefs) if llm_kind == "offline" else LiveLLM(llm_key)   # LiveLLM reads LLM from the environment
    label = "offline" if llm_kind == "offline" else f"{llm.llm_key}: {llm.provider} {llm.model}"
    approver = Approver(sc["human_answers"] if auto else None, show=echo)
    tag = name if run_index is None else f"{name}_run{run_index}"
    _archive_previous(out, out_root / "_previous" / f"{tag}_{datetime.now():%Y%m%d_%H%M%S_%f}")
    trace = Trace(out / "trace.jsonl")
    trace.log("run", "start", **_run_record(llm, llm_kind, name, run_index))

    echo(f"\n### Scenario '{name}': {sc['description']}  [llm={label}]")
    error, quota = None, False
    try:
        final = run_agent(llm, tools, CFG["base_message"] + " " + sc["extra_message"], approver, trace, echo=echo)
    except Exception as e:                  # e.g. API out of credit: log it, then go on with the next scenario
        error, quota = f"{type(e).__name__}: {e}", isinstance(e, QuotaExhausted)
        trace.log("agent", "error", output=error, ok=False)
        final = f"Run aborted by an error; nothing more was done. {error}"
    (out / "final_message.txt").write_text(final, encoding="utf-8")
    echo("\nFINAL MESSAGE:\n" + final)

    report = {"scenario": name, "run_index": run_index, "dir": out, "created": bool(tools.created), "final": final,
              "checks": None, "all_ok": None, "error": error, "quota_exhausted": quota}
    if (out / "plan.ics").exists():
        ok, results = check_files(out / "plan.ics", name)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            print(f"Checker for '{name}':")
            print_report(results)
        (out / "check.txt").write_text(buf.getvalue(), encoding="utf-8")
        echo(buf.getvalue())
        report.update(checks=results, all_ok=ok)
    trace.log("summary", "run", ok=error is None,
              **summarize(read_trace(out / "trace.jsonl"), report["created"], report["all_ok"], error))
    return report


def is_done(out: Path) -> bool:
    """A run is done when its trace ends with a summary and no error. A run that died must be redone."""
    try:
        last = read_trace(out / "trace.jsonl")[-1]
    except (OSError, IndexError, ValueError):
        return False
    return last["kind"] == "summary" and not last.get("error")


def run_batch(names: list[str], llm_kind: str, repeat: int, skip_done: bool, root: Path = ROOT / "results",
              auto: bool = True, echo=print) -> tuple[list[dict], bool]:
    """Every scenario `repeat` times for one LLM. Returns (reports, stopped_on_quota)."""
    llm_key = "offline" if llm_kind == "offline" else llm_config()[0]     # fails early if LLM or its key is missing
    base = root / llm_key
    ordered = sorted(names, key=lambda n: BATCH_ORDER.index(n) if n in BATCH_ORDER else len(BATCH_ORDER))
    reports = []
    for name in ordered:
        for k in range(1, repeat + 1):
            out = base / name / f"run{k}"
            if skip_done and is_done(out):
                echo(f"--- [{llm_key}] {name} run {k}/{repeat}: already done, skipped")
                continue
            echo(f"\n=== [{llm_key}] {name} run {k}/{repeat}")
            r = run_scenario(name, llm_kind, auto, out_root=base, echo=echo, out=out, run_index=k,
                             llm_key=None if llm_kind == "offline" else llm_key)
            reports.append(r)
            if r["quota_exhausted"]:
                echo(f"\nSTOPPED: the free quota or credit for LLM={llm_key} is used up ({r['error'][:200]}).\n"
                     f"Nothing else was run. Later, run the same command again with --skip-done to resume.")
                return reports, True
    return reports, False


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", choices=list(CFG["scenarios"]))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--llm", choices=["offline", "real", "deepseek"], default="offline",
                    help="offline = scripted stand-in; real = the model named by LLM (deepseek: old alias for real)")
    ap.add_argument("--auto", action="store_true", help="use the scripted human answers from scenarios.json")
    ap.add_argument("--out", help="output folder inside the project (default: runs, or results with --repeat)")
    ap.add_argument("--repeat", type=int, help="run each scenario N times into results/<llm>/<scenario>/run<k>/")
    ap.add_argument("--skip-done", action="store_true", help="with --repeat: skip runs that already finished")
    a = ap.parse_args()
    if a.skip_done and not a.repeat:
        ap.error("--skip-done needs --repeat")
    llm_kind = "offline" if a.llm == "offline" else "real"
    if llm_kind == "real":
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")          # fills in missing variables only: $env:LLM="b" still wins
    names = list(CFG["scenarios"]) if a.all else [a.scenario or "messy"]

    if a.repeat:
        reports, stopped = run_batch(names, llm_kind, a.repeat, a.skip_done, ROOT / (a.out or "results"),
                                     auto=a.auto or a.all)
        print("\nSUMMARY")
        for r in reports:
            print(f"  {r['scenario']:<11} run{r['run_index']}  plan written: {r['created']!s:<5}  checker: "
                  f"{'PASS' if r['all_ok'] else ('FAIL' if r['all_ok'] is False else 'n/a')}"
                  + (f"  error: {r['error'][:80]}" if r["error"] else ""))
        if stopped:
            raise SystemExit(2)
    else:
        summary = [run_scenario(n, llm_kind, a.auto or a.all, out_root=ROOT / (a.out or "runs")) for n in names]
        print("\nSUMMARY")
        for r in summary:
            print(f"  {r['scenario']:<11} plan written: {r['created']!s:<5}  checker: "
                  f"{'PASS' if r['all_ok'] else ('FAIL' if r['all_ok'] is False else 'n/a')}")

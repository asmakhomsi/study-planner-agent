"""Run the study-planner agent on one or all test scenarios, then check the result.

    python run.py --scenario messy                 # offline scripted LLM, you approve at the prompt
    python run.py --all --auto                      # all 5 scenarios, scripted approvals (no typing)
    python run.py --scenario normal --llm deepseek  # the real model (needs DEEPSEEK_API_KEY)

Outputs per scenario in <out>/<scenario>/ (default runs/): trace.jsonl, plan.ics, plan.json, final_message.txt,
check.txt. Use --out runs_offline for the offline replay so it does not overwrite the real DeepSeek runs.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
from datetime import datetime
from pathlib import Path

from checker import check_files, print_report
from planner.agent import Approver, run_agent
from planner.llm import DeepSeekLLM, ScriptedLLM
from planner.tools import StudyTools
from planner.trace import Trace

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "data" / "scenarios.json").read_text(encoding="utf-8"))


def _archive_previous(out: Path, archive: Path) -> None:
    """Move an older run's results aside: they must not look like this run's, and must never be destroyed."""
    for stale in ("trace.jsonl", "plan.ics", "plan.json", "check.txt", "final_message.txt"):
        if (out / stale).exists():
            archive.mkdir(parents=True, exist_ok=True)
            (out / stale).replace(archive / stale)


def run_scenario(name: str, llm_kind: str = "offline", auto: bool = True, out_root: Path = ROOT / "runs",
                 echo=print) -> dict:
    sc = CFG["scenarios"][name]
    prefs = dict(CFG["base_prefs"])
    out = out_root / name
    tools = StudyTools(ROOT / "data" / "scenarios" / name / "deadlines.csv", ROOT / "data" / "busy.ics", prefs, out)
    llm = ScriptedLLM(prefs) if llm_kind == "offline" else DeepSeekLLM()
    approver = Approver(sc["human_answers"] if auto else None, show=echo)
    _archive_previous(out, out_root / "_previous" / f"{name}_{datetime.now():%Y%m%d_%H%M%S}")
    trace = Trace(out / "trace.jsonl")

    echo(f"\n### Scenario '{name}': {sc['description']}  [llm={llm_kind}]")
    try:
        final = run_agent(llm, tools, CFG["base_message"] + " " + sc["extra_message"], approver, trace, echo=echo)
    except Exception as e:                  # e.g. API out of credit: log it, then go on with the next scenario
        trace.log("agent", "error", output=f"{type(e).__name__}: {e}", ok=False)
        final = f"Run aborted by an error; nothing more was done. {type(e).__name__}: {e}"
    (out / "final_message.txt").write_text(final, encoding="utf-8")
    echo("\nFINAL MESSAGE:\n" + final)

    report = {"scenario": name, "created": bool(tools.created), "final": final, "checks": None, "all_ok": None}
    if (out / "plan.ics").exists():
        ok, results = check_files(out / "plan.ics", name)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            print(f"Checker for '{name}':")
            print_report(results)
        (out / "check.txt").write_text(buf.getvalue(), encoding="utf-8")
        echo(buf.getvalue())
        report.update(checks=results, all_ok=ok)
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", choices=list(CFG["scenarios"]))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--llm", choices=["offline", "deepseek"], default="offline")
    ap.add_argument("--auto", action="store_true", help="use the scripted human answers from scenarios.json")
    ap.add_argument("--out", default="runs", help="output folder inside the project (default: runs)")
    a = ap.parse_args()
    names = list(CFG["scenarios"]) if a.all else [a.scenario or "messy"]
    summary = [run_scenario(n, a.llm, a.auto or a.all, out_root=ROOT / a.out) for n in names]
    print("\nSUMMARY")
    for r in summary:
        print(f"  {r['scenario']:<11} plan written: {r['created']!s:<5}  checker: "
              f"{'PASS' if r['all_ok'] else ('FAIL' if r['all_ok'] is False else 'n/a')}")

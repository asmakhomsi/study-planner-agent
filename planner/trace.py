"""trace.jsonl: one JSON line per step (LLM call, tool call, human decision)."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path


class Trace:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")
        self.n = 0
        self.t0 = time.time()

    def log(self, kind: str, name: str, input=None, output=None, ok: bool = True, **extra) -> None:
        self.n += 1
        row = {"step": self.n, "ts": datetime.now().isoformat(timespec="seconds"),
               "t_s": round(time.time() - self.t0, 2), "kind": kind, "name": name, "ok": ok,
               "input": _short(input), "output": _short(output), **extra}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _short(v, limit: int = 4000):
    if isinstance(v, str) and len(v) > limit:
        return v[:limit] + f"... <{len(v) - limit} more chars>"
    if isinstance(v, dict):
        return {k: _short(x, limit) for k, x in v.items()}
    if isinstance(v, list):
        return [_short(x, limit) for x in v[:100]]
    return v


def read_trace(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]

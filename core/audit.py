"""Append-only controller-level audit log (one audit.jsonl per project run)."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any


class Audit:
    def __init__(self, path: Path) -> None:
        self._fh = open(path, "a", encoding="utf-8")
        self.t0 = time.monotonic()

    def event(self, kind: str, **data: Any) -> None:
        rec = {"ts": datetime.now().isoformat(timespec="milliseconds"),
               "elapsed": round(time.monotonic() - self.t0, 3), "type": kind, **data}
        self._fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()

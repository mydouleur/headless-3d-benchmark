"""Agent runner contract: one call = one conversation round."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..audit import Audit


@dataclass
class RoundResult:
    round: int
    exit_code: int
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    error: str | None = None
    context_limited: bool = False
    final_message: str = ""
    seconds: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class AgentRunner(Protocol):
    """A headless agent CLI the controller can drive in rounds."""

    def write_config(self) -> None:
        """Materialize whatever config files the agent needs (auth, MCP, model)."""
        ...

    def run_round(self, workspace: Path, task_dir: Path, prompt: str, round_no: int,
                  audit: Audit) -> RoundResult:
        """Run one round in ``workspace``; round_no > 1 must continue the same
        conversation. Raw output must be recorded under ``task_dir / "rounds"``."""
        ...

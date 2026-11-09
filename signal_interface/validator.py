"""Signal schema & structural validator used by exporter side and GitHub Actions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from . import json_compat as orjson
from pydantic import ValidationError

from .models import SignalRecord


class SignalValidator:
    def __init__(self) -> None:
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def validate_record(self, data: Dict[str, Any]) -> Tuple[bool, SignalRecord | None]:
        try:
            record = SignalRecord.model_validate(data)
            return True, record
        except ValidationError as exc:
            self.errors.append(str(exc))
            return False, None

    def validate_jsonl(self, path: Path) -> Tuple[bool, List[SignalRecord]]:
        self.errors.clear()
        self.warnings.clear()
        valid: List[SignalRecord] = []
        seen_ids: set[str] = set()

        if not path.exists():
            self.errors.append(f"File not found: {path}")
            return False, []

        with path.open("rb") as fh:
            for lineno, raw in enumerate(fh, start=1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    data = orjson.loads(raw)
                except orjson.JSONDecodeError as exc:
                    self.errors.append(f"Line {lineno}: invalid JSON – {exc}")
                    continue

                ok, record = self.validate_record(data)
                if not ok:
                    self.errors.append(f"Line {lineno}: schema validation failed")
                    continue

                assert record is not None
                if record.signal_id in seen_ids:
                    self.errors.append(f"Line {lineno}: duplicate signal_id {record.signal_id}")
                    continue
                seen_ids.add(record.signal_id)
                valid.append(record)

        return len(self.errors) == 0, valid

    def report(self) -> str:
        lines = []
        if self.errors:
            lines.append("ERRORS:")
            lines.extend(f"  • {e}" for e in self.errors)
        if self.warnings:
            lines.append("WARNINGS:")
            lines.extend(f"  • {w}" for w in self.warnings)
        if not lines:
            return "All signals valid."
        return "\n".join(lines)

#!/usr/bin/env python3
"""Retrying GitHub Actions client for the Hudsonventura HTTPS ingress."""
from __future__ import annotations
import json, os, sys, time
from pathlib import Path
import requests


def main() -> int:
    source = Path(os.getenv("TSE_CANONICAL_SIGNALS", "state/signals_canonical.jsonl"))
    base_url = os.environ.get("TSE_DELIVERY_URL", "").rstrip("/")
    token = os.environ.get("TSE_DELIVERY_TOKEN", "")
    if not base_url or not token:
        print("TSE_DELIVERY_URL and TSE_DELIVERY_TOKEN are required", file=sys.stderr)
        return 2
    if not source.exists():
        print(f"Canonical signal file missing: {source}", file=sys.stderr)
        return 2
    signals = []
    for line_no, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            signals.append(json.loads(line))
        except json.JSONDecodeError as exc:
            print(f"Invalid JSON at {source}:{line_no}: {exc}", file=sys.stderr)
            return 2
    if not signals:
        print("No signals in this scan; nothing to deliver.")
        return 0

    payload = {"batch_id": os.getenv("GITHUB_RUN_ID", "manual"), "signals": signals}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = f"{base_url}/v1/signals"
    last_error = None
    for attempt in range(1, 6):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=(10, 60))
            response.raise_for_status()
            result = response.json()
            if result.get("invalid"):
                print(json.dumps(result, indent=2), file=sys.stderr)
                return 1
            print(json.dumps({k: v for k, v in result.items() if k not in ("accepted", "duplicates")}, indent=2))
            print(f"Hudsonventura ingress accepted={len(result.get('accepted', []))}, duplicates={len(result.get('duplicates', []))}")
            return 0
        except (requests.RequestException, ValueError) as exc:
            last_error = str(exc)
            if attempt < 5:
                time.sleep(min(2 ** attempt, 20))
    print(f"Signal delivery failed after 5 attempts: {last_error}", file=sys.stderr)
    return 1

if __name__ == "__main__":
    raise SystemExit(main())

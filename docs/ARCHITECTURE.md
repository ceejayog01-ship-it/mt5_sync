# Architecture Decision Record

## Status
Accepted

## Decision
Keep the original TSE Python engine (scan_once + states + data) as the single source of truth.
Insert a clean Signal Output Interface.
Replace Liberatti with a Hudsonventura-compatible MT5 path.
Enforce a permanent signal-equivalence audit in CI.

## Flow

```
scan_once.py  (51 strategies)
      │
      ├─► signals_out.json          (legacy shape, still written)
      │
      └─► state/signals_canonical.jsonl   (SignalRecord via adapter)
                │
                ▼
         MT5 Signal Receiver
                │
                ▼
         audit/receipts.jsonl + equivalence tests
```

## Out of scope of this repository
- Live order placement
- Always-on terminal
- Liberatti bridge
- Rewriting any of the 51 strategies into MQL5

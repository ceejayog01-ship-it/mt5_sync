# TSE → Hudsonventura MT5 Integration

**Architecture:** One original strategy engine + one signal interface + one Hudsonventura MT5 environment.

Liberatti is **completely removed**.

```
ORIGINAL TSE PROJECT (this repo)
        │
scan_once.py + states/ + data/     ← 51 BEST strategies (unchanged)
        │
SIGNAL OUTPUT INTERFACE            ← canonical SignalRecord / JSONL
        │
HUDSONVENTURA MT5 DOCKER           ← replaces liberatti/mt5bridge
        │
MT5 SIGNAL RECEIVER + equivalence audit
```

## What stays unchanged (source of truth)

| TSE component            | Decision  |
|--------------------------|-----------|
| 51 BEST strategies       | Preserve  |
| Strategy calculations    | Preserve  |
| Research parameters      | Preserve  |
| Market scanner           | Preserve  |
| Session / vol / trend filters | Preserve |
| Signal generation        | Preserve  |
| Research datasets        | Preserve  |
| Existing Python engine   | Preserve  |

No strategy is rewritten in MQL5. No second strategy engine.

## What was added

1. **Signal Output Interface** (`signal_interface/`)
   - Canonical `SignalRecord` schema (Design A / B, research_net, horizon_bars, …)
   - Adapter from the existing `scan_once` dict shape
   - JSONL exporter + schema validator

2. **MT5 Signal Receiver** (`mt5_receiver/`)
   - Validates structure, detects duplicates, records receipt status
   - Produces an append-only audit trail for the equivalence gate

3. **Signal hand-off** (`mt5_execute.py`)
   - Consumes canonical signals
   - Writes a demo ledger and the EA's input file `.data/MQL5/Files/tse_signals.csv`
   - Never sends orders itself (it has no MT5 connection)

3b. **Order placement: the EA** (`mql5/Experts/TSE_SignalExecutor.mq5`)
   - Runs inside the Hudsonventura MT5 container started by `scripts/run_mt5_executor.py`; also exports the broker M30 candles the scanner reads
   - Places real orders (`OrderSend`) on whichever account `MT5_LOGIN` / `MT5_SERVER` point to, demo or real, only when `DRY_RUN` is exactly `false`; otherwise it only logs

4. **Permanent signal-equivalence audit** (`tests/test_signal_parity.py`)
   - Exact match on Signal ID, Strategy ID, Instrument, Direction, Timestamp, Entry, SL, TP
   - No missing / duplicated / altered signals

5. **GitHub Actions + Docker**
   - `scan.yml` – 30-min weekday gate (Liberatti removed)
   - `ci.yml` – schema + parity + receiver image build
   - `Dockerfile.mt5-receiver` – lightweight receiver image

## Quick start (local)

```bash
pip install -r requirements.txt
pip install -e .

# Demonstration signals through the interface
python -m signal_interface.exporter --demo --count 6 --out example_signals/demo_batch.jsonl

# Receiver + audit
python -m mt5_receiver.receiver --input example_signals/demo_batch.jsonl --audit-dir ./audit

# Full equivalence suite
pytest tests/ -v
```

## GitHub deployment

1. Push this repository to GitHub.
2. (Optional) Set secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
3. (Optional) Set variables: `KB_PAIRS`, `KB_EXEC_PAIRS`, `KB_DRY_RUN`, …
4. Actions will run on the 30-min schedule and on every PR/push (CI gate).

**No Liberatti image is started.**  
**No always-on VPS is required.**

## Acceptance criteria

- [x] Original TSE engine remains the sole source of truth
- [x] Liberatti removed from the architecture
- [x] Every critical field matches exactly through the interface
- [x] Missing / duplicated / mutated signals fail the CI gate
- [x] Dry-run by default; live orders only when `DRY_RUN=false`

Live order execution is performed by the EA inside the Hudsonventura MT5 container, using the same canonical signals that passed the equivalence audit.


## GitHub Actions -> Hudsonventura MT5 (ephemeral, broker candles)

Each scheduled run builds (cached) and starts the vendored `hudsonventura/MT5_Docker` image on the runner (no ports, no domain, no server). The EA exports **broker M30 candles**, `scan_once.py` scans them, and the same container then trades the signals through the EA before it is stopped. Setup, secrets, cost and behaviour: [`docs/GITHUB_TO_HUDSONVENTURA.md`](docs/GITHUB_TO_HUDSONVENTURA.md).

## MT5 terminal layout (matches upstream)

`docker-compose.hudsonventura.yml` (local, with noVNC) and `docker-compose.actions.yml` (Actions, no ports) run the image built from `vendor/MT5_Docker/src` with upstream's mount (`./.data/MQL5/` -> `.../MetaTrader 5/MQL5/:ro`) plus one read-write overlay for `MQL5/Files/`. Ports `5901` (VNC) and `6901` (noVNC), `mt5.ini` at `.data/MQL5/Files/mt5.ini` (template: `mt5.ini.example`), and compilation via `docker compose -f docker-compose-mt5-compiler.yml up` all follow upstream's `src/` files. Details and one untested compile risk: [`docs/HUDSONVENTURA.md`](docs/HUDSONVENTURA.md).

# GitHub Actions -> Hudsonventura MT5 (ephemeral, no server)

Everything happens inside one scheduled workflow run. No always-on host, no domain, no open ports, no ingress/Caddy.

```text
scan.yml (cron, weekdays)
  gate -> scan_once.py (51 BEST) -> mt5_execute.py
        -> .data/MQL5/Files/tse_signals.csv   (current run's signals only)
  scripts/run_mt5_executor.py
        -> docker build of vendor/MT5_Docker/src (layer-cached), then compose up -d mt5 (no ports)
        -> container installs MT5, logs in from mt5.ini, attaches TSE_SignalExecutor via [StartUp]
        -> EA reads the CSV, trades, writes tse_processed.txt
        -> script waits for every new signal_id in tse_processed.txt, saves logs, runs `compose down`
  commit state/ (tse_processed.txt, tse_executor_ledger.txt)
```

The terminal is skipped entirely (no minutes spent) when there are no rows for the EA's symbol and no Design A time-exit is due.

## One-time setup

1. Repository secrets: `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` (use a **demo** account first).
2. Optional repository variables: `KB_EXEC_PAIRS` (default `EURUSD`; only the first pair is traded), `MT5_LOTS` (default `0.01`), `KB_DRY_RUN`. No image variable is needed: the image is built from `vendor/MT5_Docker/src` (see `vendor/MT5_Docker/UPSTREAM.md` for the pinned upstream commit).
3. Run the **Compile EA** workflow once (Actions tab). It commits `mql5/Experts/TSE_SignalExecutor.ex5`. Check the log if it fails; see the `Trade.mqh` note in `docs/HUDSONVENTURA.md`.
4. Run **TSE Scan** manually with `dry_run=true` and `force_scan=true`; read `audit/mt5_expert_journal.log` in the run artefacts. Only set `dry_run=false` once the EA journal shows what you expect on the demo account.

## Behaviour you should know

- **Live only when `DRY_RUN` is exactly `false`.** Otherwise the EA gets `InpDryRun=true` (it logs, never sends orders).
- **One symbol per run.** The EA ignores (and marks processed) signals for any symbol other than its chart.
- **Design A time-exit.** The close happens only while the EA is running. When a live Design A signal is acted on, the script records the due time in `state/tse_executor_ledger.txt`; the first run after it starts the terminal so the EA can close the position. Design B relies on the SL/TP held by the broker.
- **EA memory** (`tse_processed.txt`) is restored from `state/` at start and saved back at the end, so a re-run does not repeat a signal.
- **Credentials:** `mt5.ini` is written at run time from secrets and deleted at the end; it is never committed (`.gitignore`). The EA journal in `audit/` can contain the account number and server name: keep the repo private.
- **Cost:** the first build installs Wine and the desktop packages (slow); later runs reuse the Actions layer cache. Every run that starts the terminal still installs MT5 inside the fresh container (upstream says about 2 minutes) before it can log in. Actions caches expire after 7 days unused, which triggers a full rebuild.

## Not used any more

`mt5_receiver/http_api.py`, `Dockerfile.tse-ingress`, `deploy/Caddyfile`, `scripts/deliver_signals.py` and `tests/test_http_ingress.py` belong to the earlier HTTPS-delivery design. They are no longer referenced by any compose file or workflow and can be deleted.

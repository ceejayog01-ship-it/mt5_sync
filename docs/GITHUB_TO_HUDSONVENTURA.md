# GitHub Actions -> Hudsonventura MT5 (ephemeral, broker candles, no server)

Everything happens inside one scheduled workflow run. No always-on host, no domain, no open ports.

```text
scan.yml (cron :02/:32, weekdays, only while the session gate is open)
  build image from vendor/MT5_Docker/src (layer-cached)
  run_mt5_executor.py start
        -> compose up -d mt5, mt5.ini written from secrets, EA attached via [StartUp]
        -> EA exports BROKER M30 candles: .data/MQL5/Files/tse_candles_<PAIR>.csv + tse_candles_ready.txt
  scan_once.py   (KB_CANDLE_DIR=.data/MQL5/Files)  51 BEST on broker candles -> Telegram + signals
  mt5_execute.py -> .data/MQL5/Files/tse_signals.csv
  run_mt5_executor.py finish
        -> same container: EA trades the signals (dry-run or live), waits for tse_processed.txt,
           saves logs/state, compose down, mt5.ini deleted
  commit state/ (tse_processed.txt, tse_executor_ledger.txt)
```

The scan only ever uses candles from the broker feed the orders go to. There is no silent fallback: if a candle file is missing the scan fails (red run) instead of scanning other data.

## One-time setup

1. Repository secrets: `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` (use a **demo** account first), plus `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
2. Optional repository variables: `KB_PAIRS` (default `XAUUSD,EURUSD,GBPUSD,USDJPY,AUDUSD`, the pairs whose candles are exported), `KB_EXEC_PAIRS` (default `EURUSD`; only the first pair is traded), `SYMBOL_SUFFIX` (broker suffix such as `.m`, if your broker uses one), `MT5_LOTS` (default `0.01`), `KB_DRY_RUN`.
3. Run the **Compile EA** workflow (it commits `mql5/Experts/TSE_SignalExecutor.ex5`). Re-run it whenever the `.mq5` changes.
4. Run **TSE Scan** by hand with `test_signal=true`. Read `audit/mt5_expert_journal.log`: you should see `TSE: candles: all 5 symbols exported`, then the dry-run action for the dummy signal.
5. Set the repository variable `KB_DRY_RUN` to `false` only when you want real orders.

## Behaviour you should know

- **Live only when `DRY_RUN` is exactly `false`.** Otherwise the EA gets `InpDryRun=true` (it logs, never sends orders). `test_signal=true` always forces dry-run.
- **The terminal starts on every active scan** (it is the candle source), about 5 to 7 minutes per run including the MT5 install. Active scans are roughly 28 per weekday (London and New York sessions), so expect on the order of 3,000+ Actions minutes a month. A private repo on the free plan has 2,000 minutes; public repos are not metered.
- **Timestamps.** The EA converts broker server time to UTC (`TimeTradeServer() - TimeGMT()`, rounded to half hours) and `scan_once.py` refuses a candle file whose newest bar would close in the future.
- **One symbol per run for trading.** The EA ignores (and marks processed) signals for any symbol other than its chart. Candles are exported for every pair in `KB_PAIRS`.
- **Design A time-exit.** The close happens only while the EA is running. When a live Design A signal is acted on, the script records the due time in `state/tse_executor_ledger.txt`; the first scan after it closes the position. Design B relies on the SL/TP held by the broker.
- **EA memory** (`tse_processed.txt`) is restored from `state/` at start and saved back at the end, so a re-run does not repeat a signal.
- **Credentials:** `mt5.ini` is written at run time from secrets and deleted at the end; never committed. The EA journal in `audit/` can contain the account number and server name: keep the repo private.

## Not used any more

`mt5_receiver/http_api.py`, `Dockerfile.tse-ingress`, `deploy/Caddyfile`, `scripts/deliver_signals.py`, `tests/test_http_ingress.py` (skipped by the workflows) and `mt5_execute_liberatti_legacy.py` belong to earlier designs and can be deleted.

#!/usr/bin/env python3
"""
Start the Hudsonventura MT5 container for ONE GitHub Actions run, let the EA act on the
current signals (and close expired Design A trades), then stop it.

Skips the terminal entirely (exit 0) when there is nothing for it to do:
  * no signal rows for the EA's symbol in .data/MQL5/Files/tse_signals.csv, and
  * no Design A time-exit due (ledger: state/tse_executor_ledger.txt).

Environment:
  DRY_RUN            "false" => EA InpDryRun=false (live orders). Anything else => dry run.
  KB_EXEC_PAIRS      comma list; the FIRST pair is the one chart/symbol the EA is attached to.
  MT5_LOGIN / MT5_PASSWORD / MT5_SERVER   broker account (GitHub secrets)
  (image: tse-mt5:vendored, built from vendor/MT5_Docker/src by the workflow before this runs)

Usage:  run_mt5_executor.py --check   -> writes needed=true|false to $GITHUB_OUTPUT, starts nothing
  MT5_TIMEOUT        seconds to wait for the EA (default 900)
  MT5_LOTS           default 0.01
"""
import csv
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / ".data" / "MQL5"
FILES = DATA / "Files"
LOGS = DATA / "Logs"
STATE = ROOT / "state"
AUDIT = ROOT / "audit"
COMPOSE = ["docker", "compose", "-f", str(ROOT / "docker-compose.actions.yml")]
EA_NAME = "TSE_SignalExecutor"
PROCESSED = "tse_processed.txt"
LEDGER = STATE / "tse_executor_ledger.txt"  # one epoch per line: when a Design A trade is due to close
M30 = 1800


def log(msg):
    print(f"[mt5-executor] {msg}", flush=True)


def read_rows(symbol):
    path = FILES / "tse_signals.csv"
    if not path.exists():
        return []
    with path.open(newline="") as fh:
        return [r for r in csv.DictReader(fh) if r.get("instrument") == symbol]


def read_ledger():
    if not LEDGER.exists():
        return []
    return [int(x) for x in LEDGER.read_text().split() if x.strip().isdigit()]


def write_ledger(vals):
    STATE.mkdir(exist_ok=True)
    LEDGER.write_text("".join(f"{v}\n" for v in sorted(vals)))


def read_journal():
    """MT5 logs are UTF-16; fall back to latin-1 if not."""
    out = []
    for f in sorted(LOGS.glob("*.log")):
        raw = f.read_bytes()
        try:
            out.append(raw.decode("utf-16"))
        except UnicodeError:
            out.append(raw.decode("latin-1", "replace"))
    return "\n".join(out)


def processed_ids():
    f = FILES / PROCESSED
    return set(f.read_text().split()) if f.exists() else set()


def main():
    symbol = (os.environ.get("KB_EXEC_PAIRS") or "EURUSD").split(",")[0].strip()
    if "," in (os.environ.get("KB_EXEC_PAIRS") or ""):
        log(f"WARNING: EA handles one symbol per chart; only {symbol} will be traded")
    live = os.environ.get("DRY_RUN", "true").strip().lower() == "false"
    timeout = int(os.environ.get("MT5_TIMEOUT", "900"))
    now = int(time.time())

    rows = read_rows(symbol)
    ledger = read_ledger()
    due = [t for t in ledger if t <= now]
    log(f"symbol={symbol} live={live} new_rows={len(rows)} due_time_exits={len(due)}")
    needed = bool(rows or due)
    if "--check" in sys.argv:
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as f:
                f.write(f"needed={'true' if needed else 'false'}\n")
        return 0
    if not needed:
        log("nothing for the terminal to do; not starting MT5")
        return 0

    for k in ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER"):
        if not os.environ.get(k):
            log(f"ERROR: {k} is not set")
            return 2

    ex5 = ROOT / "mql5" / "Experts" / f"{EA_NAME}.ex5"
    if not ex5.exists():
        log(f"ERROR: {ex5} missing - run the 'Compile EA' workflow first")
        return 2

    # ---- stage files for the container (host side) ----
    (DATA / "Experts").mkdir(parents=True, exist_ok=True)
    (DATA / "Presets").mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    FILES.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ex5, DATA / "Experts" / ex5.name)
    if (STATE / PROCESSED).exists():
        shutil.copy2(STATE / PROCESSED, FILES / PROCESSED)

    (DATA / "Presets" / "tse.set").write_text(
        f"InpDryRun={'false' if live else 'true'}\n"
        f"InpLots={os.environ.get('MT5_LOTS', '0.01')}\n"
    )
    (FILES / "mt5.ini").write_text(
        "[Common]\n"
        f"Login={os.environ['MT5_LOGIN']}\n"
        f"Password={os.environ['MT5_PASSWORD']}\n"
        f"Server={os.environ['MT5_SERVER']}\n"
        "NewsEnable=0\nCertInstall=0\nServices=4294967295\n\n"
        "[Experts]\nAllowLiveTrading=1\nAllowDllImport=0\nEnabled=1\nAccount=1\nProfile=1\n\n"
        "[StartUp]\n"
        f"Expert={EA_NAME}.ex5\nSymbol={symbol}\nPeriod=M30\nExpertParameters=tse.set\n"
    )
    subprocess.run(["chmod", "-R", "a+rwX", str(DATA)], check=True)  # container user is uid 1000

    # ---- run ----
    rc = 0
    started = time.time()
    try:
        subprocess.run(COMPOSE + ["up", "-d", "--pull", "never", "mt5"], check=True)
        want = {r["signal_id"] for r in rows}
        inited = False
        grace_until = None
        while time.time() - started < timeout:
            time.sleep(15)
            journal = read_journal()
            inited = f"{EA_NAME} init" in journal
            if want:
                if want <= processed_ids():
                    log("all new signals handled by the EA")
                    break
            elif inited:
                grace_until = grace_until or time.time() + 120  # let ManageOpenTrades run
                if time.time() >= grace_until:
                    log("EA up and time-exit pass done")
                    break
        else:
            log(f"ERROR: timed out after {timeout}s (EA init seen: {inited})")
            rc = 1
    finally:
        AUDIT.mkdir(exist_ok=True)
        subprocess.run(COMPOSE + ["logs", "--no-color", "mt5"], stdout=(AUDIT / "mt5_container.log").open("w"), stderr=subprocess.STDOUT)
        (AUDIT / "mt5_expert_journal.log").write_text(read_journal())
        if (FILES / PROCESSED).exists():
            STATE.mkdir(exist_ok=True)
            shutil.copy2(FILES / PROCESSED, STATE / PROCESSED)
        subprocess.run(COMPOSE + ["down", "-t", "20"])
        # never leave credentials on disk or in artefacts
        (FILES / "mt5.ini").unlink(missing_ok=True)

    if rc == 0:
        # ledger: drop exits we just serviced; add Design A trades opened now (live only)
        keep = [t for t in ledger if t > now]
        if live:
            for r in rows:
                if r.get("design") == "A":
                    h = int(r.get("horizon_bars") or 48)
                    keep.append(int(time.time()) + h * M30 + 600)
        write_ledger(keep)
    return rc


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Run the Hudsonventura MT5 container for ONE GitHub Actions scan, in two phases that share the
same container:

  start   stage files, start the container, wait until the EA has exported broker M30 candles
          (MQL5/Files/tse_candles_<PAIR>.csv + tse_candles_ready.txt). The scan then reads them.
  finish  after scan_once.py / mt5_execute.py wrote MQL5/Files/tse_signals.csv: wait until the EA
          has handled the new signals (and any due Design A time-exit), save logs and state, stop
          the container, delete mt5.ini.

Environment:
  DRY_RUN            "false" => EA InpDryRun=false (live orders). Anything else => dry run.
  KB_PAIRS           pairs whose candles the EA exports (default XAUUSD,EURUSD,GBPUSD,USDJPY,AUDUSD)
  KB_EXEC_PAIRS      comma list; the FIRST pair is the one chart/symbol the EA trades.
  SYMBOL_SUFFIX      broker symbol suffix, if any (e.g. ".m")
  MT5_LOGIN / MT5_PASSWORD / MT5_SERVER   broker account (GitHub secrets)
  MT5_LOTS           default 0.01
  MT5_TIMEOUT        seconds to wait for candles in `start` (default 600)
  MT5_ACT_TIMEOUT    seconds to wait for the EA in `finish` (default 300)
Image: tse-mt5:vendored (built from vendor/MT5_Docker/src by the workflow before `start`).
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
READY = "tse_candles_ready.txt"
LEDGER = STATE / "tse_executor_ledger.txt"   # one epoch per line: when a Design A trade is due to close
M30 = 1800


def log(msg):
    print(f"[mt5-executor] {msg}", flush=True)


def exec_symbol():
    return (os.environ.get("KB_EXEC_PAIRS") or "EURUSD").split(",")[0].strip()


def is_live():
    return os.environ.get("DRY_RUN", "true").strip().lower() == "false"


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


def container_running():
    r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", "tse-mt5"],
                       capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == "true"


def _sh(cmd, timeout=40):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return f"(failed: {e})"


def diag(label):
    """Print enough of the container's state into the step log to see where it is stuck."""
    import re
    print(f"::group::MT5 diagnostics ({label})", flush=True)
    out = _sh(["docker", "logs", "--tail", "400", "tse-mt5"])
    lines = [l.strip() for l in re.split(r"[\r\n]+", out) if l.strip() and not re.search(r"[\u2800-\u28ff]", l)]
    print("-- container log (spinner lines removed, last 25):\n" + "\n".join(lines[-25:]))
    print("-- wine/MT5 processes:\n" + _sh(["docker", "exec", "tse-mt5", "bash", "-c",
          "pgrep -af 'terminal64|MetaEditor|mt5setup|wineserver|vnc' | cut -c1-160 || true"]))
    print("-- every MQL5 folder in the wine prefix:\n" + _sh(["docker", "exec", "tse-mt5", "bash", "-c",
          "find /home/headless/.wine/drive_c -maxdepth 8 -type d -name MQL5 2>/dev/null || true"]))
    mt5_sh = r"""
MT5='/home/headless/.wine/drive_c/Program Files/MetaTrader 5'
echo "--- ls MetaTrader 5"; ls -la "$MT5" 2>&1 | head -40
echo "--- ls MQL5 / Experts / Presets (as the terminal sees them)"; ls -la "$MT5/MQL5" "$MT5/MQL5/Experts" "$MT5/MQL5/Presets" 2>&1 | head -40
echo "--- terminal logs (newest 2 files, last 40 lines each)"
ls -t "$MT5"/logs/*.log 2>/dev/null | head -2 | while read -r f; do echo "== $f"; iconv -f UTF-16LE -t UTF-8 "$f" 2>/dev/null | tail -40; done
echo "--- MQL5/Logs (EA journal) newest file"
ls -t "$MT5"/MQL5/Logs/*.log 2>/dev/null | head -1 | while read -r f; do echo "== $f"; iconv -f UTF-16LE -t UTF-8 "$f" 2>/dev/null | tail -40; done
echo "--- /tmp/wine.log (fixme lines removed, last 25)"; grep -vi fixme /tmp/wine.log 2>/dev/null | tail -25
echo "--- Config/common.ini (passwords removed)"; grep -ihv password "$MT5/Config/common.ini" 2>/dev/null | head -30
"""
    print("-- inside the container:\n" + _sh(["docker", "exec", "tse-mt5", "bash", "-c", mt5_sh], timeout=60))
    print("-- host .data/MQL5/Files:\n" + _sh(["ls", "-la", str(FILES)]))
    print("-- host .data/MQL5/Logs:\n" + _sh(["ls", "-la", str(LOGS)]))
    j = read_journal().strip().splitlines()
    print("-- EA/terminal journal (last 40):\n" + "\n".join(j[-40:]) if j else "-- EA/terminal journal: (empty)")
    print("::endgroup::", flush=True)


def container_exists():
    return subprocess.run(["docker", "inspect", "tse-mt5"], capture_output=True).returncode == 0


def collect_and_stop():
    """Save logs/state, stop the container, remove credentials.
    Safe to call twice: a second call must not overwrite the logs the first call saved."""
    AUDIT.mkdir(exist_ok=True)
    if container_exists():
        with (AUDIT / "mt5_container.log").open("w") as fh:
            subprocess.run(COMPOSE + ["logs", "--no-color", "mt5"], stdout=fh, stderr=subprocess.STDOUT)
    journal = read_journal()
    if journal or not (AUDIT / "mt5_expert_journal.log").exists():
        (AUDIT / "mt5_expert_journal.log").write_text(journal)
    if (FILES / PROCESSED).exists():
        STATE.mkdir(exist_ok=True)
        shutil.copy2(FILES / PROCESSED, STATE / PROCESSED)
    subprocess.run(COMPOSE + ["down", "-t", "20"])
    (FILES / "mt5.ini").unlink(missing_ok=True)   # never leave credentials on disk or in artefacts


# ---------------------------------------------------------------------------
def start():
    symbol = exec_symbol()
    suffix = os.environ.get("SYMBOL_SUFFIX", "")
    pairs = os.environ.get("KB_PAIRS") or "XAUUSD,EURUSD,GBPUSD,USDJPY,AUDUSD"
    if "," in (os.environ.get("KB_EXEC_PAIRS") or ""):
        log(f"WARNING: EA handles one symbol per chart; only {symbol} will be traded")
    timeout = int(os.environ.get("MT5_TIMEOUT", "600"))

    for k in ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER"):
        if not os.environ.get(k):
            log(f"ERROR: {k} is not set")
            return 2
    ex5 = ROOT / "mql5" / "Experts" / f"{EA_NAME}.ex5"
    if not ex5.exists():
        log(f"ERROR: {ex5} missing - run the 'Compile EA' workflow first")
        return 2

    for d in (DATA / "Experts", DATA / "Presets", LOGS, FILES):
        d.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ex5, DATA / "Experts" / ex5.name)
    if (STATE / PROCESSED).exists():
        shutil.copy2(STATE / PROCESSED, FILES / PROCESSED)
    (FILES / READY).unlink(missing_ok=True)

    (DATA / "Presets" / "tse.set").write_text(
        f"InpDryRun={'false' if is_live() else 'true'}\n"
        f"InpLots={os.environ.get('MT5_LOTS', '0.01')}\n"
        f"InpExportSymbols={pairs}\n"
        f"InpSymbolSuffix={suffix}\n"
    )
    (FILES / "mt5.ini").write_text(
        "[Common]\n"
        f"Login={os.environ['MT5_LOGIN']}\n"
        f"Password={os.environ['MT5_PASSWORD']}\n"
        f"Server={os.environ['MT5_SERVER']}\n"
        "NewsEnable=0\nCertInstall=0\nServices=4294967295\n\n"
        "[Experts]\nAllowLiveTrading=1\nAllowDllImport=0\nEnabled=1\nAccount=1\nProfile=1\n\n"
        "[StartUp]\n"
        f"Expert={EA_NAME}.ex5\nSymbol={symbol}{suffix}\nPeriod=M30\nExpertParameters=tse.set\n"
    )
    subprocess.run(["chmod", "-R", "a+rwX", str(DATA)], check=True)  # container user is uid 1000

    log(f"starting container (live={is_live()}, trade symbol={symbol}{suffix}, candles for {pairs})")
    subprocess.run(COMPOSE + ["up", "-d", "--pull", "never", "mt5"], check=True)

    started = time.time()
    last_note = 0
    while time.time() - started < timeout:
        time.sleep(10)
        if (FILES / READY).exists():
            log(f"broker candles ready after {int(time.time() - started)}s: {(FILES / READY).read_text().strip()}")
            return 0
        if not container_running():
            log("ERROR: container stopped before candles were exported")
            break
        if time.time() - last_note > 120:
            last_note = time.time()
            log(f"waiting for candles... {int(time.time() - started)}s")
            diag(f"{int(time.time() - started)}s")
    else:
        log(f"ERROR: no candles after {timeout}s (login, EA attach or history download problem)")
    diag("failure")
    collect_and_stop()
    return 1


def finish():
    symbol = exec_symbol()
    live = is_live()
    act_timeout = int(os.environ.get("MT5_ACT_TIMEOUT", "300"))
    now = int(time.time())

    if not container_running():
        log("container is not running (start failed or never ran); cleaning up")
        collect_and_stop()
        return 0

    rows = read_rows(symbol)
    ledger = read_ledger()
    due = [t for t in ledger if t <= now]
    want = {r["signal_id"] for r in rows}
    log(f"symbol={symbol} live={live} new_rows={len(rows)} due_time_exits={len(due)}")

    rc = 0
    try:
        started = time.time()
        if want:
            while time.time() - started < act_timeout:
                time.sleep(10)
                if want <= processed_ids():
                    log("all new signals handled by the EA")
                    break
                if not container_running():
                    log("ERROR: container stopped while waiting for the EA")
                    rc = 1
                    break
            else:
                log(f"ERROR: EA did not handle {sorted(want - processed_ids())} within {act_timeout}s")
                rc = 1
        elif due:
            log("giving the EA 45s for the Design A time-exit pass")
            time.sleep(45)
    finally:
        collect_and_stop()

    if rc == 0:
        keep = [t for t in ledger if t > now]
        if live:
            for r in rows:
                if r.get("design") == "A":
                    h = int(r.get("horizon_bars") or 48)
                    keep.append(int(time.time()) + h * M30 + 600)
        write_ledger(keep)
    return rc


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "start":
        sys.exit(start())
    if cmd == "finish":
        sys.exit(finish())
    print(__doc__)
    sys.exit(2)

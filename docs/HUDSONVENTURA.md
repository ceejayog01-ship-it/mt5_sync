# How to get Hudsonventura MT5 Docker docs & endpoints

## Official sources

| Resource | URL |
|----------|-----|
| Docker Hub image | https://hub.docker.com/r/hudsonventura/mt5 |
| GitHub repo + README | https://github.com/hudsonventura/MT5_Docker |
| docker-compose example | https://raw.githubusercontent.com/hudsonventura/MT5_Docker/refs/heads/main/src/docker-compose.yml |
| MQL5 compiler compose | https://raw.githubusercontent.com/hudsonventura/MT5_Docker/refs/heads/main/src/docker-compose-mt5-compiler.yml |
| Terminal startup script | https://github.com/hudsonventura/MT5_Docker/blob/main/src/start.sh |
| Compile script | https://github.com/hudsonventura/MT5_Docker/blob/main/src/build.sh |
| Dockerfile | https://github.com/hudsonventura/MT5_Docker/blob/main/src/Dockerfile |
| Example mt5.ini | https://github.com/hudsonventura/MT5_Docker/blob/main/src/mt5.ini |
| MetaTrader .ini docs | https://www.metatrader5.com/en/terminal/help/start_advanced/start |

## What Hudsonventura actually provides

Read directly from a clone of the upstream repo (commit `3537cc0`, "Relocate mt5.ini configuration to MQL5/Files, update Docker image to v2.3"). The layout below is taken from upstream's `src/Dockerfile`; Docker Hub was never queried.

Image built from the vendored `vendor/MT5_Docker/src` (upstream's published tag is `hudsonventura/mt5:2.3`; we no longer pull it):

- Ubuntu 24.04 + Openbox + TigerVNC + noVNC 1.4.0 + `winehq-staging` (`src/Dockerfile`), runs as user `headless`, `WORKDIR /home/headless`
- **MetaTrader 5 is not baked into the image.** `/start.sh` downloads `mt5setup.exe` and installs to `/home/headless/.wine/drive_c/Program Files/MetaTrader 5` on first start (about 2 minutes; repeated on every new container because `.wine` is not a volume)
- `CMD ["/bin/bash", "/start.sh"]`; `/build.sh` is also copied to the image root
- Ports `5901` (VNC) and `6901` (noVNC, `http://localhost:6901/vnc.html`), both published on all host interfaces by upstream's compose
- Env vars read by the scripts: `VNC_PW` (default `password`), `SCREEN_RESOLUTION` (default `1024x768`)
- Optional auto-login: `/start.sh` runs `terminal64.exe /config:"C:\Program Files\MetaTrader 5\MQL5\Files\mt5.ini"`
- Compiler: `/build.sh` runs `MetaEditor64.exe /compile:'C:/MQL5/Experts/' /include:'C:/MQL5/' /log`

It does **not** expose a Liberatti-style REST trading API (`/order/open`, `/copy_rates_from_pos`, ...).

So the correct integration with our architecture is **file-based**:

```
TSE Python engine
      |
Signal Output Interface  ->  state/signals_canonical.jsonl
      |
mounted folder  ./.data/MQL5/Files/
      |
MQL5 EA inside Hudsonventura  ->  places / manages orders
```

## Mounts (`docker-compose.hudsonventura.yml`, service `mt5`)

Copied from upstream `src/docker-compose.yml`:

```yaml
volumes:
  - ./.data/MQL5/:/home/headless/.wine/drive_c/Program Files/MetaTrader 5/MQL5/:ro
  #- ./.data/MQL5/servers.dat:/home/headless/.wine/drive_c/Program Files/MetaTrader 5/Config/servers.dat
```

Upstream mounts the **whole** MQL5 folder read-only from `./.data/MQL5/`. This repo adds exactly one line on top:

```yaml
  - ./.data/MQL5/Files/:/home/headless/.wine/drive_c/Program Files/MetaTrader 5/MQL5/Files/
```

Reason: `TSE_SignalExecutor.mq5` writes `tse_processed.txt` into `MQL5/Files/` and silently skips the write if it fails (`SaveProcessedIds`). With a read-only Files folder, `restart: always` would make the EA forget handled signal IDs. `mt5_execute.py` writes the signal CSV to this same host folder. (`docker-compose.actions.yml` also overlays `MQL5/Logs/` read-write so the EA journal can be read.)

Layout on the host:

```
.data/MQL5/
  Experts/    <- put the compiled .ex5 here (the terminal reads this tree)
  Files/      <- mt5.ini, tse_signals.csv, tse_processed.txt
  Include/    (optional)
```

Note: upstream's own README says `./data/MQL5/Files/mt5.ini` (no leading dot) and refers to `srv/mt5.ini`; the compose file and the real example are `./.data/...` and `src/mt5.ini`.

## Quick start (terminal only)

```bash
mkdir -p .data/MQL5/Experts .data/MQL5/Files
docker compose -f docker-compose.hudsonventura.yml up -d mt5

# browser access:
# http://localhost:6901/vnc.html?password=my_vnc_password
```

Change `VNC_PW` first. Ports 5901/6901 are open on all interfaces; firewall them or bind to `127.0.0.1` (see the comment in the compose file).

## Auto-login (.ini)

Copy `mt5.ini.example` to `.data/MQL5/Files/mt5.ini` and edit the first lines:

```ini
[Common]
Login=123456
Password=YourStr0ngP@ss
Server=Test-MT5-Demo
```

`/start.sh` already passes this file via `/config:`; its "Applying configuration" step does nothing else. The file contains broker credentials and is git-ignored (`.data/MQL5/`, `mt5.ini`). Upstream's full example with every key is `src/mt5.ini`.

If login fails for a broker not in upstream's pre-searched list, search for the server once in the GUI and persist `servers.dat` with the commented volume line.

## Compile an EA (no GUI)

Uses upstream's separate file `docker-compose-mt5-compiler.yml` (build context changed to the vendored folder). Build the image first: `docker build -t tse-mt5:vendored vendor/MT5_Docker/src`:

```bash
mkdir -p mql5/Experts mql5/Include
# TSE_SignalExecutor.mq5 is already in mql5/Experts/

docker compose -f docker-compose-mt5-compiler.yml up
# TSE_SignalExecutor.ex5 appears next to the .mq5 in mql5/Experts/

# the terminal reads a different tree, so deploy it:
mkdir -p .data/MQL5/Experts
cp mql5/Experts/TSE_SignalExecutor.ex5 .data/MQL5/Experts/
```

The compiler mounts `./mql5/Experts/` -> `C:/MQL5/Experts/` and `./mql5/Include/` -> `C:/MQL5/Include/:ro` (not the Program Files tree), which is why the copy step is needed.

**Untested risk:** `TSE_SignalExecutor.mq5` has `#include <Trade/Trade.mqh>`, and `build.sh` passes `/include:'C:/MQL5/'`, where only your mounted `mql5/Include/` exists (it is empty in this repo). If compilation reports `Trade.mqh` not found, copy the stock `Include/Trade/` folder from an installed terminal into `mql5/Include/`, or compile once from MetaEditor over VNC. This was not run here because MT5/Wine cannot be installed in the authoring environment.

## Implications for this TSE repo

1. **Candle data** for `scan_once.py` must come from somewhere else (CSV via `KB_M30_CSV`, a separate data bridge, or a different MT5 API image). Hudsonventura alone does not feed Python rates.
2. **Order execution** should be done by an MQL5 EA that reads the canonical JSONL (or a simplified signal file) from `MQL5/Files/`.
3. **Equivalence audit** in this repo still validates that every TSE signal is preserved before anything is handed to that EA.

If you later need a REST bridge in front of MT5, that is a different product (e.g. meta-docker, mt5linux, mt5-docker-api). Hudsonventura is the terminal + compiler environment only.

---

## File-drop trade path (implemented)

### How it works

1. Run the vendored image with `./.data/MQL5/` mounted (`docker-compose.hudsonventura.yml`).
2. Signal Interface + `mt5_execute.py` write:
   - `state/signals_canonical.jsonl` (audit / equivalence)
   - `.data/MQL5/Files/tse_signals.csv` (EA input; `mql5/Files/tse_signals.csv` is a sample copy)
3. EA `TSE_SignalExecutor.mq5` polls the CSV and calls normal MQL5 trade functions.
4. Design A: SL only, time-exit after `horizon_bars` × M30.
5. Design B: SL + TP from the signal.

### CSV format (`tse_signals.csv`)

```
signal_id,instrument,direction,design,entry,sl,tp,horizon_bars
TSE-A-EURUSD-...,EURUSD,BUY,A,1.08750,1.08420,,48
TSE-B-EURUSD-...,EURUSD,BUY,B,1.08750,1.08420,1.09300,48
```

Design A has an **empty** `tp` field (never `0.0`).

### Attach the EA

1. Compile and deploy the `.ex5` (previous section), then `docker compose -f docker-compose.hudsonventura.yml up -d`
2. Browser: `http://localhost:6901/vnc.html?password=my_vnc_password`
3. Log into your broker (or use `.data/MQL5/Files/mt5.ini`).
4. Open the symbol chart (e.g. EURUSD M30).
5. Drag `TSE_SignalExecutor` onto the chart.
6. Keep **InpDryRun = true** until demo behaviour looks correct, then set false.

On GitHub Actions the EA is attached automatically (`[StartUp]` in the generated `mt5.ini`; see `docs/GITHUB_TO_HUDSONVENTURA.md`). Only `.data/MQL5/Files/tse_signals.csv` is seen by the terminal. `mt5_execute.py` also writes `mql5/Files/tse_signals.csv`, which is a tracked sample copy and is not mounted.

### Generate a drop-file from Python

```bash
PYTHONPATH=. python -m signal_interface.exporter --demo --count 4 --out state/signals_canonical.jsonl
# then run the demonstration executor (writes the CSV):
PYTHONPATH=. python mt5_execute.py
# or only the CSV writer:
PYTHONPATH=. python -c "
from signal_interface.exporter import _demo_signals
from signal_interface.ea_drop import write_ea_csv
from pathlib import Path
write_ea_csv(_demo_signals(4), Path('mql5/Files/tse_signals.csv'))
write_ea_csv(_demo_signals(4), Path('.data/MQL5/Files/tse_signals.csv'))
print('written')
"
```

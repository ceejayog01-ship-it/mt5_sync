#!/usr/bin/env python3
"""
One-time helper: make the MT5 container "search for" your broker so its server lands in servers.dat,
then save that file as config/servers.dat (the scan mounts it read-only on every run).

MT5 cannot log in to a broker whose server it has not searched for once (upstream README, "After install").
This drives the terminal's "Open an Account" dialog with xdotool and takes a screenshot after every step
(audit/screen_reg_*.png) so the sequence can be corrected from the pictures.

Environment: BROKER_SEARCH (default "Headway"), plus the usual MT5_LOGIN / MT5_PASSWORD / MT5_SERVER.
Writes changed=true|false to $GITHUB_OUTPUT.
"""
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_mt5_executor as m  # noqa: E402

IN_CONTAINER = "/home/headless/.wine/drive_c/Program Files/MetaTrader 5/Config/servers.dat"


def gx(cmd, timeout=40):
    return m._sh(["docker", "exec", "tse-mt5", "bash", "-c", f"DISPLAY=:1 {cmd}"], timeout=timeout)


def geometry():
    out = gx("xdotool getactivewindow getwindowgeometry --shell")
    v = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
    try:
        g = [int(v[k]) for k in ("X", "Y", "WIDTH", "HEIGHT")]
        if g[2] > 200 and g[3] > 200:
            return g
    except (KeyError, ValueError):
        pass
    return [137, 89, 750, 590]       # centred guess for a 1024x768 screen


def servers_info():
    out = m._sh(["docker", "exec", "tse-mt5", "bash", "-c",
                 f"stat -c%s '{IN_CONTAINER}' 2>&1; sha256sum '{IN_CONTAINER}' 2>&1 | cut -c1-64"])
    return out.replace("\n", " | ")


def main():
    company = os.environ.get("BROKER_SEARCH", "Headway")
    rc, tools = m.bring_up(use_persisted=False)
    if rc:
        return rc
    if not tools:
        m.log("ERROR: xdotool/wmctrl could not be installed")
        m.collect_and_stop()
        return 1

    t0 = time.time()
    while time.time() - t0 < 300:
        time.sleep(10)
        m.close_dialogs()
        if any(w in gx("wmctrl -l") for w in ("Netting", "Hedging", "MetaTrader")):
            break
    time.sleep(25)
    m.close_dialogs()
    before = servers_info()
    m.log(f"servers.dat before: {before}")
    m.screenshot("reg_0_start")

    # 1. select "Accounts" in the Navigator and press Insert ("Open an Account")
    gx("xdotool mousemove 60 266 click 1")
    time.sleep(1)
    gx("xdotool key Insert")
    time.sleep(4)
    m.screenshot("reg_1_dialog")

    # 2. search for the broker
    gx(f"xdotool type --delay 120 '{company}'")
    gx("xdotool key Return")
    time.sleep(8)
    m.screenshot("reg_2_search")

    # 3. pick the first company in the list, then Next (positions are fractions of the dialog)
    X, Y, W, H = geometry()
    m.log(f"dialog geometry {X},{Y} {W}x{H}")
    gx(f"xdotool mousemove {X + int(.13 * W)} {Y + int(.29 * H)} click 1")
    time.sleep(1)
    m.screenshot("reg_3_selected")
    gx(f"xdotool mousemove {X + int(.78 * W)} {Y + int(.94 * H)} click 1")
    time.sleep(10)
    m.screenshot("reg_4_next")

    # 4. close the terminal gracefully so it writes servers.dat, then fetch the file
    gx("wmctrl -c 'Netting'; wmctrl -c 'Hedging'")
    time.sleep(20)
    after = servers_info()
    m.log(f"servers.dat after:  {after}")
    out = m.AUDIT / "servers.dat"
    m.AUDIT.mkdir(exist_ok=True)
    subprocess.run(["docker", "cp", f"tse-mt5:{IN_CONTAINER}", str(out)])
    changed = out.exists() and "No such file" not in after and before != after
    if changed:
        dst = m.SERVERS_SRC
        dst.parent.mkdir(exist_ok=True)
        dst.write_bytes(out.read_bytes())
        m.log(f"servers.dat CHANGED: saved to config/servers.dat ({dst.stat().st_size // 1024} KB, "
              f"sha256 {hashlib.sha256(dst.read_bytes()).hexdigest()[:16]})")
    else:
        m.log("servers.dat did not change: look at audit/screen_reg_*.png to see where the dialog sequence stopped")
    m.diag("register-end")
    m.collect_and_stop()
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a") as f:
            f.write(f"changed={'true' if changed else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

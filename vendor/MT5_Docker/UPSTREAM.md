# Vendored upstream

- Source: https://github.com/hudsonventura/MT5_Docker
- Commit: `3537cc0f4a7ad91c1cc7d2645f0af9b6ceee36cf` ("Relocate mt5.ini configuration to MQL5/Files, update Docker image to v2.3, ...")
- Copied, unmodified: the whole upstream tree at that commit: `src/` (Dockerfile, start.sh, build.sh, index.html, mt5.ini, upstream compose files), `README.md`, `assets/`, and the root `servers.dat` (1.7 MB).
- `servers.dat` here is a reference copy only: `src/start.sh` still downloads it from upstream's GitHub at run time when the installed one is missing or small.
- Licence: upstream had no LICENSE file at this commit. Confirm the author's terms before making this repo public.

## Updating
```bash
git clone https://github.com/hudsonventura/MT5_Docker /tmp/up
rm -rf vendor/MT5_Docker/src vendor/MT5_Docker/assets
cp -r /tmp/up/src /tmp/up/assets vendor/MT5_Docker/ && cp /tmp/up/README.md /tmp/up/servers.dat vendor/MT5_Docker/
# update the commit hash above, run "Compile EA", then a dry-run scan
```
Do not edit files under `src/`; keep local changes in the compose files so updates stay a plain copy.

## Still downloaded at build/run time (not vendored)
- Build: `ubuntu:24.04`, Ubuntu packages, WineHQ repo (dl.winehq.org), noVNC v1.4.0 and websockify v0.11.0 from GitHub.
- Run: `mt5setup.exe` from download.mql5.com (cannot be vendored), and `servers.dat` from github.com/hudsonventura/MT5_Docker (only if missing).

## Local changes (re-apply after every update of src/)
- `src/Dockerfile`: Wine is pinned to 11.21 (the version every working run used). Wine 11.22 (released mid-November 2026)
  made the MT5 installer die at start.sh step 7/9, after which start.sh waits forever. The Vendor workflow overwrites
  src/, so re-apply this edit if you run it.

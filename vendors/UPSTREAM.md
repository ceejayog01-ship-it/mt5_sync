# Vendored upstream

- Source: https://github.com/hudsonventura/MT5_Docker
- Commit: `3537cc0f4a7ad91c1cc7d2645f0af9b6ceee36cf` ("Relocate mt5.ini configuration to MQL5/Files, update Docker image to v2.3, ...")
- Copied: the whole `src/` folder, unmodified (Dockerfile, start.sh, build.sh, index.html, mt5.ini, upstream compose files).
- Not copied: README.md, assets/, root `servers.dat` (`start.sh` downloads it from upstream GitHub at runtime).
- Licence: upstream had no LICENSE file at this commit. Confirm the author's terms before making this repo public.

## Updating
```bash
git clone https://github.com/hudsonventura/MT5_Docker /tmp/up
rm -rf vendor/MT5_Docker/src && cp -r /tmp/up/src vendor/MT5_Docker/src
# update the commit hash above, run "Compile EA", then a dry-run scan
```
Do not edit files under `src/`; keep local changes in the compose files so updates stay a plain copy.

## Still downloaded at build/run time (not vendored)
- Build: `ubuntu:24.04`, Ubuntu packages, WineHQ repo (dl.winehq.org), noVNC v1.4.0 and websockify v0.11.0 from GitHub.
- Run: `mt5setup.exe` from download.mql5.com, `servers.dat` from github.com/hudsonventura/MT5_Docker.

# AIOS runtime — systemd topology

One box, three units. Installed by `scripts/install_services.sh` (idempotent, run as
root on the box). `scripts/deploy.sh` restarts the long-running pair on every deploy.

| Unit | What | Lifecycle |
|---|---|---|
| `aios-dispatch.service` | gunicorn → `core.dispatch:app` on **127.0.0.1:8000** (the `/dispatch` ingress + `/dash` dashboard) | always-on, `Restart=always` |
| `aios-worker.service` | `python -m core.worker` — claims jobs, runs module handlers, beats heartbeat | always-on, `Restart=always` |
| `aios-watchdog.timer` → `.service` | `python -m core.watchdog` — probes, orphan reaper, meter alerts, dead-man ping | oneshot every 30 min (+jitter), `Persistent=true` |

## Why loopback-only

The spec's invariant is TLS-only inbound. Until a TLS proxy (Caddy + a DNS name)
fronts the box, gunicorn binds `127.0.0.1` and **nothing listens publicly**. Access
is by SSH tunnel:

```bash
ssh -N -L 8000:127.0.0.1:8000 root@<box-ip>     # leave running
open http://localhost:8000/dash                  # dashboard
```

When DNS + Caddy land, the only change is a Caddyfile (`reverse_proxy 127.0.0.1:8000`)
— the units don't change.

## Secrets

`/opt/aios/.env` (0600, root-only) is created once by the installer with a generated
`DISPATCH_BEARER_TOKEN`; owner API keys are appended there by hand. The installer
**never overwrites it**. Units load it via `EnvironmentFile=`. Read the token:

```bash
ssh root@<box-ip> "grep DISPATCH_BEARER_TOKEN /opt/aios/.env"
```

## Operating

```bash
systemctl status aios-dispatch aios-worker            # state
journalctl -u aios-dispatch -u aios-worker -f          # live logs
journalctl -u aios-watchdog --since "-2h"              # recent watchdog runs
systemctl list-timers aios-watchdog.timer              # next probe
```

The worker unit is intentionally un-sandboxed: the HyperFrames render path
(chromium via npx, `/dev/shm`, the `.studio` workspace) was proven on the box
without systemd confinement, and a paid render is the wrong place to discover a
sandbox regression. Dispatch and watchdog are hardened (`NoNewPrivileges`,
`PrivateTmp`, `ProtectSystem=full`).

**Exactly ONE `aios-worker` per box.** The render workspace composes into a fixed
`index.html`, so concurrent renders would clobber each other — the single worker
process is what makes renders serial. Scaling produce means per-render workspace
dirs first (see the invariant note in `hyperframes_render.py`), not a second unit.

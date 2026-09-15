"""One reading of what this box does on its own — for the doctor (CLI) and the machine GUI.

`snapshot()` is THE registry query. The doctor prints it; page 1 of the GUI renders it; a
test asserts the doctor's lines come from it. Two independent walks of `worker.PERIODIC`
plus the config gates is how a CLI and a GUI end up disagreeing about what is enabled
(OSDev0, docs/MACHINE_GUI_SPEC.md §"one source, not two").

Each row:
  name        the timer's registered name, or the recipe's slug
  kind        "timer" (worker.PERIODIC) | "recipe" (an installed `kind:` pack, ridden by gtm_recipes)
  interval_s  seconds between runs (a recipe reports its carrier's interval; 0 = no carrier here)
  runnable    recipes only: False when this box registers no RECIPE_CARRIER timer, so nothing
              will ever run the pack however its gate is set
  gate        the config key that turns it on ("gtm.outbound_sending", "machines.<slug>.enabled"),
              or None when the timer has no gate (it always runs)
  state       "on" | "off" | "dry" | "live" | "ungated"
  note        a short human phrase ("ships dark", "plans, spends nothing")
plus `warnings`: strings the operator must see (a pack prefix collision the boot degraded around).
"""
from __future__ import annotations

from core.config import as_bool, get_config

# timer-name prefix → (config section, flag). A timer whose name starts with the key is gated by it.
GATES = {
    "gtm": ("gtm", "outbound_sending"), "written": ("written", "enabled"),
    "carousel": ("carousel", "enabled"), "leadmagnet": ("leadmagnet", "enabled"),
    "newsletter": ("newsletter", "enabled"), "autopost": ("autopost", "enabled"),
}
RECIPE_CARRIER = "gtm_recipes"


def snapshot(cfg: dict | None = None, *, load: bool = True) -> dict:
    """{"rows": [...], "warnings": [...]}. `load=True` imports every module so PERIODIC is full
    (what the worker would run); pass False when the caller already loaded them."""
    from core import worker
    if load:
        worker.load_modules()
    cfg = get_config() if cfg is None else cfg
    rows, warnings = [], []
    carrier = 0
    for t in sorted(worker.PERIODIC, key=lambda x: x["name"]):
        n = t["name"]; iv = int(t.get("interval", t.get("interval_s", 0)) or 0)
        if n == RECIPE_CARRIER:
            carrier = iv
        key = next((k for k in GATES if n.startswith(k)), None)
        if key:
            sec, flag = GATES[key]
            on = as_bool((cfg.get(sec) or {}).get(flag))
            rows.append({"name": n, "kind": "timer", "interval_s": iv, "gate": f"{sec}.{flag}",
                         "state": "on" if on else "off", "note": ""})
        elif n.startswith("machine:"):
            slug = n.split(":", 1)[1]
            on = as_bool(((cfg.get("machines") or {}).get(slug) or {}).get("enabled"))
            rows.append({"name": n, "kind": "timer", "interval_s": iv, "gate": f"machines.{slug}.enabled",
                         "state": "on" if on else "off", "note": "" if on else "ships dark"})
        else:
            rows.append({"name": n, "kind": "timer", "interval_s": iv, "gate": None, "state": "ungated", "note": ""})
    try:
        from core import packs
        try:
            from marketing.lead_machine import plug as _plug
            _plug._registered_prefixes()                    # strict: names a collision boot degraded around
        except ImportError:
            pass
        except Exception as e:  # noqa: BLE001
            warnings.append(f"{e} — remove one of them; the boot kept the first it saw")
        for m in packs.discover():
            if "kind" not in m:
                continue
            c = (cfg.get("machines") or {}).get(m["slug"]) or {}
            if not as_bool(c.get("enabled")):
                state, note = "off", "ships dark"
            elif as_bool(c.get("dry_run"), default=True):
                state, note = "dry", "plans, spends nothing"
            else:
                state, note = "live", ""
            # NO CARRIER, NO RUN — and it must not say otherwise. Recipe packs do not schedule
            # themselves; they are ridden by the RECIPE_CARRIER timer, which marketing/lead_machine
            # registers. A box that ships no Lead Machine has no carrier, so `carrier` is 0 and
            # nothing on that box will ever run this pack. Until 2026-09-15 the row still reported
            # state "live" and the doctor still printed "via gtm_recipes  ON, LIVE" — naming a timer
            # that does not exist on that box, for work that cannot happen. Measured inside an
            # exported customer_voice box: a machine pack the customer had PAID for and switched on
            # rendered as ON, LIVE with interval_s 0.
            #
            # This reports the truth. It does not make the pack run — giving an inbox box a carrier
            # is a product decision about what a bought machine does there, not a display fix.
            rows.append({"name": m["slug"], "kind": "recipe", "interval_s": carrier,
                         "gate": f"machines.{m['slug']}.enabled",
                         "state": state, "note": note,
                         "runnable": bool(carrier)})
    except Exception as e:  # noqa: BLE001 — a bad pack set must not blank the whole view
        warnings.append(f"could not list recipes: {e}")
    return {"rows": rows, "warnings": warnings}


def render(snap: dict) -> list[str]:
    """The doctor's lines, from one snapshot. Kept here so the CLI and any test render alike."""
    out = []
    for w in snap["warnings"]:
        out.append(f"    ✗ {w}")
    for r in snap["rows"]:
        if r["kind"] == "recipe":
            st = {"off": "off (ships dark)", "dry": "ON, dry_run (plans, spends nothing)", "live": "ON, LIVE"}[r["state"]]
            if r.get("runnable", True):
                out.append(f"    recipe {r['name']:<21} via {RECIPE_CARRIER}  {st}")
            else:
                # The gate's state is still shown — the operator did switch it on, and hiding that
                # would be its own lie — but it is shown as what it is: switched on, never run.
                out.append(f"    recipe {r['name']:<21} NOT RUNNABLE HERE "
                           f"(no {RECIPE_CARRIER} timer on this box)  {st}")
        else:
            tail = {"on": "  ON", "off": "  off" + ("  (ships dark)" if r["note"] else ""), "ungated": ""}[r["state"]]
            out.append(f"    {r['name']:<28} every {r['interval_s']:>6}s{tail}")
    return out

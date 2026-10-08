"""The first thing a machine's unit runs: say hello to the box, import the machine, then do the work the box sends.

Runs INSIDE the unit as `/usr/bin/python3 -I -S /opt/machine/sdk/boot.py` (core/machine_sandbox/unit.py), so the
standard library only. The machine's own package is imported from /opt/machine/code as `my_machines.<slug>`, the
same name the in-process loader gives it, so its relative imports work the same way.

A MACHINE THAT CANNOT START SAYS WHY AND EXITS NON-ZERO. systemd restarts it a few times, then stops trying
(StartLimitBurst), and the box shows the reason it logged here.
"""
import importlib.util
import os
import sys
import traceback
import types

sys.path.insert(0, "/opt/machine/sdk")
sys.dont_write_bytecode = True

from core import sdk  # noqa: E402

CODE = "/opt/machine/code"


def _log(level, event, **fields):
    try:
        sdk._door().call("log", level=level, event=event, fields=fields)
    except Exception:                                   # noqa: BLE001 — a log line never stops a machine
        pass


def _import(slug: str):
    name = "my_machines." + slug.replace("-", "_")
    parent = types.ModuleType("my_machines")
    parent.__path__ = []
    sys.modules["my_machines"] = parent
    spec = importlib.util.spec_from_file_location(name, os.path.join(CODE, "__init__.py"),
                                                  submodule_search_locations=[CODE])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    slug = os.environ.get("OWNBOX_MACHINE", "")
    door = sdk._door()
    door.call("hello", sdk=sdk.VERSION, slug=slug)
    try:
        _import(slug)
    except Exception as e:                              # noqa: BLE001 — the reason goes to the owner
        _log("error", "import_failed", error=f"{type(e).__name__}: {str(e)[:300]}",
             where=traceback.format_exc(limit=3)[-600:])
        return 1
    _log("info", "started", jobs=sorted(sdk._JOBS))
    while True:
        job = (door.call("next", wait=30) or {}).get("job")
        if not job:
            continue
        fn = sdk._JOBS.get(job.get("name"))
        try:
            if fn is None:
                raise LookupError(f"no job named {job.get('name')!r} in this machine")
            fn()
            door.call("done", run=job["run"], ok=True)
        except Exception as e:                          # noqa: BLE001 — a failed run is reported, never fatal
            door.call("done", run=job["run"], ok=False, error=f"{type(e).__name__}: {str(e)[:300]}")


if __name__ == "__main__":
    sys.exit(main())

"""Inside a machine's sandbox, `core` is only the SDK (core/machine_sandbox/guest/core/sdk.py). The box's own code
is not in the unit at all, so `from core import sdk` is the one import a sandboxed machine can make of ours."""

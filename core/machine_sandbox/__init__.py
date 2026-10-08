"""Where a custom machine from a repository runs: its own user, its own locked unit, one door to the box.

docs/SCOPE_CUSTOM_MACHINES_FROM_A_REPO.md (version 2), build step 1. A machine pulled from a repository is code
nobody on our team wrote, so it must not run inside the box's own processes (core/custom_machines.py loads into
them with the box's full privileges). Here it runs as:

  * its own non-root user, in a transient systemd unit whose filesystem holds only its code (read-only), its data
    folder and the guest SDK (unit.py, decision D1);
  * with NO network: no address family but AF_UNIX, a private network namespace, and every IP denied (D2);
  * reaching the box only through one Unix socket of its own, served by the box (broker.py, D3). The box knows
    the caller by the socket it came in on AND the peer's user id from the kernel, never by anything the caller
    says;
  * the same Python API as the in-process SDK (`from core import sdk; m = sdk.machine(...)`), served inside the
    unit by guest/core/sdk.py, so a machine runs unchanged on either.

Step 1 carries the walls and the door: handshake, small settings, the data folder, scheduled jobs and logging.
Every other SDK call arrives in later steps behind permissions (step 2). Nothing here installs a machine on a box:
that is step 4, after the hostile-machine suite passes (tests/test_machine_sandbox_walls.py).
"""

# my/machines/ — your own machines

Each machine you build lives in its own folder here: `my/machines/<name>/`, self-contained. The box
loads a folder only when it has both of these, and says why it skipped one if it does not:

- `machine.yaml` — `name`, `version` and `requires_foundation`. The `name` is lowercase letters,
  digits and hyphens, and it is the folder's own name.
- `__init__.py` — registers what the machine does when it is loaded.

Nothing here is ever touched by an update, and nothing here stops one. Keep every file a machine
needs inside its folder: a machine that is one folder can be copied to another box as it is.

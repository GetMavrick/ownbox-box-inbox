# Changelog

What changed for you, newest first. `git pull` takes an update; this says what it was.

## 1.3 — 2026-09-05
- **Your box mints its own keys.** `install.sh` now creates `DISPATCH_BEARER_TOKEN` as well as
  the signing key. Before this, a fresh box had an empty bearer — so `/dispatch` refused every
  request and the dashboard login refused every password, with nothing on screen saying why.
- **The README tells you the one command.** It documented four hand-run steps that skipped
  `install.sh` entirely, which is how the empty-key problem stayed reachable. `bash
  scripts/install.sh` is the install; the manual path is named as the trap it is.
- **A separate dashboard password.** Set `DASH_TOKEN` and the dashboard stops accepting your
  command credential. Showing someone the dashboard no longer means handing them the box.
  Leave it unset and nothing changes.
- **One shared dashboard shell** (`core/dash`), so every machine's pages look and behave the
  same instead of each growing its own copy.
- **`python scripts/doctor.py` shows what runs.** One reading of the schedule — every timer,
  whether it is on, and every installed recipe as dark / dry / live.
- **Recipes are credited with what they find.** A business records which recipe found it
  first, so you can see what each one produced and what it cost per business. A business two
  recipes both find belongs to the one that paid to find it.
- The doctor warns when your dashboard would send its password over plain HTTP.

## 1.2 — 2026-09-05
- **Machine packs:** a purchased machine is a folder with a `machine.yaml`; `python
  scripts/add_machine.py <folder>` installs it (refuses if its host core machine is not here,
  records it in `licence.json`, ships it dark). The worker discovers packs by manifest.
- **A second core machine plugs into the one you have:** `bash scripts/install.sh --into
  <your-aios>` from the second box — one `core/`, one database, one wall.
- **The kill switch:** `python scripts/halt.py` stops every timer and job within seconds;
  `scripts/resume.py` reverses it. A file (`data/PAUSED`), so it survives a restart.
- **`my/knowledge/`:** every `.md` there rides on every brain call, in every machine — the facts
  about your business. Capped at ~40 KB with a warning, not a bill.
- **Export everything:** `python scripts/export_data.py` writes every table that holds your rows
  as CSV and JSON under `data/export-<time>/`, with a manifest.
- `machine.yaml` is the pack contract (`docs/MACHINE_PACK_SPEC.md`); `core/FOUNDATION_VERSION` is 1.1.

## 1.1 — 2026-09-05
- **One-command install:** `bash scripts/install.sh` (venv, package, `.env`, signing key,
  licence, database, proof, then the doctor). Re-running it is safe.
- **The doctor:** `scripts/doctor.py` — one screen: what this box is, which keys are set and
  what each missing one blocks, what runs on a schedule and whether it is on, the machine's
  own checks, the database, and the exact fix for every gap.
- This file.

## 1.0 — 2026-09-04
- **The AIOS foundation ships in every box:** an empty wall (`DEVSTATE.md`) and its post
  script, the operating doctrine (`CLAUDE.md`), an agent role template, and `my/` for
  everything you edit — with `my/settings.yaml` as your overlay so updates never touch
  your choices.
- **Department layout:** `marketing/lead_machine`, `marketing/content_machine`;
  vendor adapters under `core/vendors`; suites under `tests/`.
- **The box thinks on your key:** `brain.backend: api` (never our subscription).
- **Your billing window, not ours:** the spend governor's cycle day and timezone are yours
  to set; shipped as UTC / day 1.
- **Opt-out links are signed with your own key:** a box with no `UNSUB_SIGNING_KEY` refuses
  to send rather than sign with a default.
- **The shipped config keeps its comments** (every knob explained in place).
- The unsubscribe page, the national-chain list, the litestream installer and the Written
  lane's thresholds file ship — each was missing from an earlier cut.

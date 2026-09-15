# The Unified Inbox Machine — setup instructions

*The box type, the package name and the licence product id are still `customer-voice-machine` — an internal
name; every file name and command below is correct as written.*

Follow these top to bottom. Every step has a command and a way to know it worked. Nothing
here assumes you have done this before. If a step fails, the section at the end says what
to look at first.

**Three rules before you start**

1. **Your keys are yours.** The box reasons on *your* Anthropic API key. We never see it,
   and this document never asks you to send it to anyone.
2. **GitHub is not optional.** Every install — on your laptop or on a server — lives in a
   *private* GitHub repo you own. That is how you get updates, how support reads your
   config without your secrets, and how a server gets the code. Step 2 sets it up.
3. **Nothing in this machine writes to a customer.** It reads and it reports. There is no
   switch to find, because there is no code here that posts.

## 0. What you need

| | Why |
|---|---|
| A computer (Mac, Windows or Linux) | To unpack, set up GitHub, and either run it here or push it to a server |
| A free GitHub account — github.com/signup | Required for everyone (rule 2) |
| **Your website address** | The whole uptime and speed half of this machine is about the site you name. Without it those rails say "connect" and report nothing |
| An Anthropic API key — console.anthropic.com → API keys | The box reasons on your key. This machine runs no model itself, but the operating system around it does |
| **Optional:** a free PageSpeed API key | Google's anonymous pool is usually empty. A free key gives your box a quota of its own |
| **Optional:** a Google Places key | Only needed for competitor standings. One metered lookup per competitor per month |
| **Recommended:** a VPS — Ubuntu 24.04, 1 vCPU / 1 GB, root access ($6–12/month) | Uptime checks are worth little from a laptop that closes. This is the one machine that really wants a server |

## 1. Unpack

You received `customer-voice-machine-<version>.zip` (or `.tar.gz` — same contents; the
version in the name is whatever we sent you). Inside it is one folder, `aios` — the same
name whichever machine you bought, because every machine is the same operating system with
fewer modules switched on. When you add a machine later it goes into this same folder and
this same repo; nothing to rebuild.

**Mac** — double-click the file. Move the `aios` folder into your home folder so it sits at
`~/aios`. Open **Terminal** (⌘-space, type Terminal) and type:

```bash
cd ~/aios
ls
```

**Windows** — right-click → *Extract All…* → extract to `C:\Users\<you>\customer-voice`.
Open **Terminal** (Start, type Terminal) and type:

```powershell
cd ~\customer-voice
dir
```

**Linux** — `tar -xzf customer-voice-machine-*.tar.gz -C ~ && cd ~/aios`

You should see `README.md`, `setup-instructions.md`, `core`, `marketing`, `config`,
`scripts`. Keep this Terminal open; every command below is typed here.

## 2. GitHub — required for everyone

**2a. Install git** if `git --version` says it is not found: Mac — it offers to install
the command-line tools; say yes. Windows — git-scm.com/download/win, keep every default.

**2b. Create a private repo.** github.com → **+** (top right) → *New repository* → name it
`aios` → **Private** → *Create repository*. Do **not** tick "add a README". Leave that page
open.

**2c. Put the box in it.** In your Terminal, in the `aios` folder:

```bash
git init
git add .
git status
```

**Stop and read the `git status` output.** It must NOT list `.env`, anything ending in
`.db`, or `.venv`. The box ships a `.gitignore` that keeps those out. If you see them, do
not continue — something removed `.gitignore`; restore it from the zip.

```bash
git commit -m "initial box"
git branch -M main
git remote add origin https://github.com/<your-username>/aios.git
git push -u origin main
```

The first push asks you to sign in. Choose *sign in with your browser* if offered. If it
asks for a password instead, GitHub wants a **token**, not your password: github.com →
Settings → Developer settings → Personal access tokens → *Tokens (classic)* → Generate →
tick **repo** → copy it → paste it as the password. Keep it; the server needs it in 3B.

Reload the GitHub page. You should see the folder. **Click through to confirm there is no
`.env` file.** There will not be, but look — this is the one mistake that matters.

## 3. Choose: run it here, or on a server

For this machine the answer is usually **a server**. An uptime check that only runs while
your laptop is open is not an uptime check. Start local if you want to see it work; move
to a server before you rely on it. The GitHub repo is what moves.

### 3A. Local

**One command does the rest.** In the `aios` folder:

```bash
bash scripts/install.sh
```

(Windows: run it in **Git Bash**, which the GitHub step installed. Same command.)

It installs Python's environment, creates `.env` from the example, generates your signing
key, stamps your licence (it asks your name and the order reference), creates the database,
runs the first proof, and ends on **the doctor** — one screen of what is set, what is
missing, and the exact command to fix each. Re-running it later is safe: it never
overwrites your `.env`, licence, or data.

Everywhere below, Windows users replace `.venv/bin/python` with `.venv\Scripts\python`.

### 3B. Server (VPS)

Create an Ubuntu 24.04 droplet/instance (1 vCPU / 1 GB). Note its IP. Then from your
Terminal:

```bash
ssh root@<server-ip>
git clone https://github.com/<your-username>/aios.git /opt/aios
bash /opt/aios/scripts/bootstrap.sh
```

Use your GitHub username and the **token** from step 2 as the password. `bootstrap.sh`
installs Python, the environment, the package and the systemd services, then **stops on
purpose** with a message about `.env` — that is step 4, done on the server (`cd /opt/aios`,
then continue below). Re-running it later is safe.

## 4. Configure

Two files. `.env` holds secrets and is never committed. `my/settings.yaml` holds your
values and is merged over the shipped config, so an update from us never overwrites them.

```bash
cp .env.example .env
nano .env        # Mac/Linux/server.  Windows: notepad .env
```

Fill only this one to start. Every other line can stay blank — each says what it is for.

```
ANTHROPIC_API_KEY=        # yours
```

**Then name your site.** This is the setting that turns the machine on. Open
`my/settings.yaml` and add:

```yaml
customer_voice:
  site_url: "https://yourbusiness.com"
  rails_owned: [uptime, pagespeed]
```

`rails_owned` is you telling the box which rails this business actually has. A rail you do
not list renders nothing at all — that is deliberate, and it is why this box will never nag
you to connect a Yelp listing you are never going to have.

**Optional — a PageSpeed quota of your own.** Google's anonymous pool is shared by everyone
calling without a key and is usually empty by mid-morning. A free key from the Google Cloud
console fixes it. Add it under the same section:

```yaml
customer_voice:
  pagespeed:
    api_key: "your-free-key"
```

**Optional — competitors.** Name them and the rail switches itself on; naming them *is* the
answer, so there is no second setting to find. `self` is your own listing, because the
comparison is the entire point. Each entry is a search phrase — a name plus enough place to
be unambiguous.

```yaml
customer_voice:
  competitors:
    self: "Your Business, Your City"
    roster:
      - "A Competitor, Your City"
      - "Another One, Your City"
```

That rail costs one metered Google Places lookup per name per month and needs a Places key
in `.env`. Leave the roster empty and it costs nothing and says so.

Then stamp your licence and create the (empty) database:

```bash
.venv/bin/python scripts/licence_stamp.py --product customer-voice-machine --buyer "Your Name or Co" --order "the order or gift ref you were given"
.venv/bin/python -c "from core import state; state.init_db()"
```

Commit what you changed — **`.env` will not be included, and that is correct**:

```bash
git add . && git commit -m "configured" && git push
```

## 5. Prove it before you trust it

```bash
.venv/bin/python tests/test_schema_integrity.py    # the database is whole
.venv/bin/python tests/test_customer_voice.py      # the machine's own suite
.venv/bin/python scripts/doctor.py                 # what is still missing, in plain words
```

Green means the box you have is the box we built. The doctor is the one to re-run whenever
something feels wrong: it says, in plain words, what is missing and the command that fixes
it. It also prints the schedule — every timer this box runs on its own, and how often.

## 6. First run

**Start the worker** and leave it running:

```bash
.venv/bin/python -m core.worker              # laptop: leave this Terminal open
systemctl enable --now aios-worker && journalctl -u aios-worker -f    # server
```

That is the whole of it. There is no queue to prime and no list to import. Within fifteen
minutes the first uptime check lands; within a day the first PageSpeed score does.

**Where to look.** Your morning page carries a Unified Inbox section from the first check
onward. Before the first check it says *waiting for its first check* — which is a true
statement about a box that started five minutes ago, not a fault.

**What the states mean.** *Connect* means it is waiting on you. *We could not reach it
since Tuesday* means it is waiting on something else and names the day it last worked.
Silence from a rail means you did not list it in `rails_owned`.

## 7. What you actually bought

A machine, and the operating system it runs on. Most of this folder is the second part, and
it is the part that keeps paying after the first machine is running.

**The shared brain.** `core/brain.py` is the one place anything reasons — every machine
calls it, nothing calls an AI vendor directly. That is what makes your spend countable and
the model swappable in one line of config. It runs on your key.

**The one network door.** `core/net.py` is the only way anything in this box reaches the
internet. A machine that reaches out through one door can be reasoned about; one that
reaches out eight ways cannot.

**The wall.** `DEVSTATE.md` is where your agents tell each other things. It ships empty on
purpose. Context does not survive a session ending; files do.

**The rules.** `CLAUDE.md` is read by Claude Code every time it opens this folder: the
layout, the invariants, and what an agent must never do without asking you. Edit it as you
grow.

**The department layout.** Departments at the top (`marketing/` today), machines inside
them, plug-ins inside those. Add `operations/` or `finance/` when you have a machine to put
in one — never before, because an empty folder is a promise the system does not keep.

### Adding the next machine

A machine is a package under a department, listed in `modules:` in
`config/aios.config.yaml`. The rule that makes this safe is one-way dependency: a new
machine imports the kernel and the door it was given, never another machine's internals.
That is what lets you add one without touching the one you already have working.

## 8. Getting updates

We send fixes as a patch or a pull request to *your* repo. To take one:

```bash
git pull
.venv/bin/pip install -e .          # only needed if the patch says so
```

On a server, then `systemctl restart aios-worker`.

The rails that are not in this box yet — reviews, comments, search — arrive this way. When
they do, they will appear on your morning page as *connect*, waiting on you to link the
account, rather than switching themselves on.

## 9. When something is wrong

1. `.venv/bin/python scripts/doctor.py` — it names the missing thing and the fix.
2. `journalctl -u aios-worker -n 200` on a server; on a laptop, the Terminal the worker is
   running in. The worker says why it refused.
3. **Your site shows as down and you know it is up.** The check runs from your box. Confirm
   the box itself has working DNS and outbound HTTPS before you look at the site.
4. **Speed says "quota used up".** That is Google's shared anonymous pool, not a fault in
   your box. Add a free PageSpeed key (step 4) and it clears.
5. `.venv/bin/python scripts/check_budget.py` — the cost guard stops the whole box at the
   monthly cap; this shows where the money went.
6. `.venv/bin/python scripts/backup_db.py` before you try anything drastic. To stop
   everything at once: `.venv/bin/python scripts/halt.py "why"`, and
   `.venv/bin/python scripts/resume.py` to start again.
7. To take your data out at any time: `.venv/bin/python scripts/export_data.py`.
8. Then reach us — the licence says how. Send the output of step 1, never your `.env`.

## Where your data lives — and the one way it can leave

Everything this machine reads — conversations, messages, reports, and your mail once Gmail is
connected — is in one file on this server: `aios.db`. **By default it goes nowhere.** The
off-box backup service is installed but **left switched off** until you fill in all four
`OBJECT_STORE_*` keys in `.env` (bucket, endpoint, key, secret). The moment you do, the database
is continuously copied to **that bucket** — one you own, on a provider you chose (Cloudflare R2,
Backblaze B2, DigitalOcean Spaces). Nobody else holds a copy; there is no bucket of ours.

So the honest sentence is: *your data lives on your server, and in a backup bucket you own if
you turn one on.* Turn it on — a server with no backup is one disk failure away from losing
every conversation — but know what you are turning on: after Gmail is connected, that bucket
holds your mail. Treat its credentials the way you treat your mailbox password.

Credentials themselves — API keys, the Gmail app password when that ships — live only in
`.env`, which is **never** part of the database and is never replicated.


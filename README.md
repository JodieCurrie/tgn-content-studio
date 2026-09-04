# TGN Content Studio

A real, multi-user content planning and production system for That's Good
News — built from your Excel calendar and live Google Sheet, not just a
copy of them. See the bottom of this file for **"What Claude actually
built vs. couldn't build"** if you want the honest technical caveats up
front.

## What this is

- Idea → Campaign → Outputs → Tasks → Deadlines → Publish, as real linked
  database rows, not spreadsheet cells.
- A calendar-first UI with your colour-coded content types, drag-and-drop
  rescheduling, and a recurring-schedule engine that understands the
  difference between "move this one" and "move the whole rhythm."
- Real accounts, real roles (Admin / Production / Final Edit / Music /
  Team Member), a shared database everyone sees the same data in.
- Everything configurable from the Admin screens: content types, colours,
  task templates, scheduling rules, the "+ Create Content" menu, users.

## Tech stack, and why

**Flask (Python) + SQLite + server-rendered HTML + vanilla JavaScript.**
No React, no Node build step, no npm packages at all on the client side.

This wasn't the first choice — the original plan was Next.js + Postgres +
Prisma, described earlier in this project's chat history. Partway through
building, it turned out the sandbox this was built in has no network
access to npm or PyPI (an infrastructure restriction, not a design
choice), so that stack couldn't actually be installed or tested. Rather
than hand you code nobody had run, everything was rebuilt on packages
that were already available, end to end, and tested against real
scenarios (see "Testing" below).

The upside: Flask + SQLite is a completely legitimate — even good —
choice at TGN's actual scale. A handful of users, a content calendar, low
write volume. SQLite handles that easily as long as its file lives on a
persistent disk, and it removes an entire moving part (a separate
database server) from what you have to pay for and maintain. If the team
grows a lot later, the data layer is a thin, deliberately un-clever
wrapper (`app/db.py`) specifically so swapping in Postgres later is a
contained change, not a rewrite.

## The data model

Everything scheduled — even a single filler carousel post — is a
**Campaign** (the idea/topic) with one or more **Content Outputs** (the
platform-specific deliverables: an Instagram Reel, the matching YouTube
long-form video, a highlight snippet). Each output carries its own
platforms, status, and assignee. Each campaign can carry any number of
**Tasks** — separate people, separate due dates, separate from the
publish date. **Task Templates** on each **Content Type** are what
auto-generate those tasks. **Scheduling Rules** describe a recurring
pattern ("targeted campaign every 14 days from this Wednesday",
"testimony on the last Tuesday of the month") and materialize real,
editable Campaign rows on a rolling horizon — nothing about a generated
campaign is read-only or fake. **Dependencies** (`depends_on_campaign_id`)
are what let a highlight snippet know it follows a specific targeted
campaign, so dragging the parent can cascade to it.

Full schema: `app/schema.sql`.

## The scheduling engine (the part worth understanding)

`app/scheduling.py` is the most important file in this codebase. Dragging
a campaign that's tied to a recurring rule always asks what "moving"
should mean:

1. **Move this campaign only** — breaks it off the rule as a one-off
   exception; the rule keeps generating everything else on schedule.
2. **Move this and its dependent outputs** — same as above, but any
   highlight snippets / follow-ups tied to it move by the same amount.
3. **Shift the recurring schedule from here onward** — updates the rule's
   anchor date; every future occurrence that hasn't already been
   individually customised regenerates on the new rhythm.

This was tested directly (drag campaign A three days later while keeping
campaign B and C on their original Wednesdays; separately, shift the
whole rule and confirm B and C move) and both behave correctly — see
"Testing" below.

## Testing

Before calling this done, all 8 scenarios from the original brief were
run against the real API, not just eyeballed:

1. Create a targeted campaign → outputs generated (short + long), tasks
   generated per role with correct due dates, colour-coded correctly.
2. Drag it 3 days with dependents → outputs, highlight-snippet
   follow-ups, and every task's due date shifted by exactly 3 days,
   preserving each task's lead time relative to the new publish date.
3. Moving one occurrence leaves the recurring rule and the next scheduled
   occurrence untouched.
4. Shifting the rule from one occurrence onward deletes and regenerates
   every later non-customised occurrence on the new 14-day rhythm.
5. A flexible filler post (e.g. a carousel) moves with no prompt at all —
   only rule-linked content asks.
6. Logged in as the Production role: calendar and tasks are visible,
   admin screens redirect away, creating content is blocked (403), and
   updating your own assigned task's status works.
7. Testimony lands on the actual last Tuesday of the month, and creating
   one automatically spawns its end-of-month blog companion the next day.
8. An idea in the Ideas Bank converts into a real scheduled campaign via
   "Schedule idea."

A couple of real bugs were caught and fixed in the process (a foreign-key
constraint that broke rule-shifting when a shifted occurrence had
dependents) — the fixes are in `app/schema.sql`'s `ON DELETE CASCADE`
clauses.

## Running it locally

```bash
pip install -r requirements.txt
cp .env.example .env        # edit SECRET_KEY at minimum
python scripts/seed.py      # creates the database + reference data + demo users
python run.py                # http://localhost:5000
```

Seeded logins (all use the password `TGNstudio2026!` — **change this
immediately**, from Account in the sidebar, once you're in):

- `jodiecurrie3.jc@gmail.com` — Admin
- `lizzy@demo.tgncontent.local` — Production (rename/re-email this to
  Lizzy's real address from Admin → Users, or just add her fresh)
- `gabe@demo.tgncontent.local` — Final Edit / Review (your workflow's
  "Gabe" handoff step, found in your live sheet's deadline columns)

No musician account is seeded — add whoever that is from Admin → Users
whenever you're ready; the Music role is fully wired up (task templates,
permissions) and waiting.

## Deploying it for real

You'll need two free accounts, a few minutes each:

**1. GitHub** (github.com) — a place to store the code so a host can
deploy from it.
- Create an account, create a new repository (e.g. `tgn-content-studio`),
  and push this folder to it:
  ```bash
  git remote add origin https://github.com/<you>/tgn-content-studio.git
  git branch -M main
  git push -u origin main
  ```

**2. Render** (render.com) — free tier is enough to start; ~$7/month if
you want the app to never spin down between visits, plus ~$1/month for a
persistent disk to store the database and uploaded files permanently.
- Sign up, choose "New → Web Service," connect your GitHub repo.
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn wsgi:app`
- Add an environment variable `SECRET_KEY` set to a long random string
  (Render can generate one for you).
- Add a **Disk**: mount path `/var/data`, and set the environment
  variable `DATABASE_PATH` to `/var/data/tgn.db` — this is what makes
  your data survive restarts and deploys.
- After the first deploy, open Render's Shell tab for the service once
  and run `python scripts/seed.py` to create the database and reference
  data.
- Render gives you a URL like `tgn-content-studio.onrender.com`
  immediately. Pointing `tgncontent.com` at it afterward is a DNS change
  in whichever registrar you buy the domain from (ask me when you get
  there — I'll write the exact records).

**Costs, all-in:** $0/month to try it (free Render web service, though it
sleeps after inactivity and takes ~30s to wake up), or about $8/month for
an always-on instance with a permanent disk, plus ~$12–15/year for the
domain if you want `tgncontent.com` specifically instead of the free
`onrender.com` address.

**Backups:** the whole database is one file (`tgn.db`) on that disk.
Render's paid disks snapshot automatically; you can also download the
file yourself from the Shell tab at any time. Ask me to add a scheduled
backup-to-email/Drive step once you're live if you want extra peace of
mind.

## Coming back to improve it

This is meant to be maintained, not shipped once and abandoned. Come back
to a Claude Code / Cowork session pointed at this same GitHub repo and
just ask in plain English — "add a content type called Bible
Mythbusters," "give the videographer a separate filming calendar," "add
a hook field to every video" — the same way this was built. Nothing here
requires touching raw SQL by hand; most of what you'll want to change
(content types, task templates, scheduling rules, the create-content
menu) is already editable from the Admin screens without any code change
at all.

## What Claude actually built vs. couldn't build

Being direct about this rather than letting it surface later:

- **Built and tested:** everything in Phase 1 of the brief (auth, roles,
  database, calendar, content creation, colour coding, campaigns,
  outputs, tasks, notes, inspiration links, file attachments,
  drag-and-drop, basic + advanced recurring scheduling), plus most of
  Phase 2 (automatic task generation, dependent date shifting, the
  three-way recurring-drag logic, content frequency/variety tracking,
  smart filler suggestions, scheduling warnings, campaign relationships).
- **Not built:** Phase 3 external integrations (Google Calendar, Gmail,
  Instagram/TikTok/YouTube posting, cloud storage). The architecture
  supports adding these later — they just weren't part of a usable Phase
  1, per the brief's own instruction not to let Phase 3 delay it.
- **Not actually deployed to a live URL** by Claude — that needs your own
  GitHub and Render accounts, per the steps above. Nobody can stand up
  hosting under your name without you creating it.
- **The stack changed mid-build** from the originally-proposed
  Next.js/Postgres/Prisma to Flask/SQLite, for the network-access reason
  explained above — flagged in the chat when it happened, not discovered
  quietly later.

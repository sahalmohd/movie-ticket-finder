# Odyssey 70mm watcher

Pushes a phone notification the moment Cinemark opens a new on-sale day for
**The Odyssey IMAX 70MM** at Cinemark Dallas XD and IMAX (11819 Webb Chapel Rd,
Dallas — the only IMAX 70mm venue in Texas).

Runs on a GitHub Actions schedule (`.github/workflows/watch.yml`) so it works
even when your computer is off. State (the current on-sale "frontier" date)
is committed to `state.json` after every run, so the commit history is a log
of every drop.

## How it works

Cinemark's theater page (`cinemark.com/theatres/tx-dallas/cinemark-dallas-xd-and-imax?showDate=YYYY-MM-DD`)
is server-rendered HTML — no JS, no auth, no API key needed. `check.py`:

1. Reads `state.json` for the current known on-sale frontier date.
2. Fetches `frontier + 1`. If The Odyssey IMAX 70MM (`CinemarkMovieId=104867`)
   has no showtime block there, nothing changed — exit quietly.
3. If it does, walks forward day by day until a date comes back empty, to
   find how many new days opened.
4. Sends one push via [ntfy.sh](https://ntfy.sh) listing every new day, its
   showtimes, and direct seat-map links, then updates the frontier.

This costs a single HTTP request per run in steady state, since Cinemark adds
dates contiguously.

## One-time setup

1. **Install ntfy** on your phone: [ntfy.sh](https://ntfy.sh) (iOS/Android app).
2. **Pick a topic name** — long and random, e.g. `odyssey70mm-x7k2p9qz`. Anyone
   who knows the topic name can read and publish to it, so don't use anything
   guessable.
3. **Subscribe** to that topic in the app.
4. **Set the repo secret**, so the topic itself never appears in any file or
   commit:
   ```bash
   gh secret set NTFY_TOPIC
   ```
   (paste the topic name when prompted — this keeps it out of shell history).
5. Push this repo to GitHub (public, so Actions minutes are free/unlimited).
   The workflow starts running on its `*/10 * * * *` schedule automatically.

## Manual runs

```bash
# Test the parser against saved fixtures — no network call
python3 check.py --self-test

# Real check without sending a notification or writing state.json
python3 check.py --dry-run

# Real check, will notify and commit state.json if something changed
python3 check.py

# Trigger a workflow run on GitHub directly
gh workflow run watch.yml
```

## Forcing a re-alert / resetting

To make the watcher re-scan and notify as if starting fresh, edit `state.json`
back to an earlier `frontier` date (or `null` to fully re-bootstrap) and
commit. The next scheduled run will pick it up.

## Timing expectations

GitHub Actions' `schedule` trigger is queued, not punctual — a `*/10` cron
commonly fires 5–20 minutes late at peak, occasionally more. That's fine for
"a whole new day opened" (six showtimes go on sale at once), but if you ever
need to catch a single showtime the instant it's released, move this to a
Cloudflare Worker with Cron Triggers instead (1-minute granularity, free
tier) — the polling logic in `check.py` ports over as-is.

## Reference: verified page structure (as of 2026-07-30)

| Fact | Value |
|---|---|
| Theater slug | `tx-dallas/cinemark-dallas-xd-and-imax` |
| Theater id | `207` |
| Movie id (IMAX 70mm cut) | `104867` — do **not** match on title, there are 3 separate Odyssey listings at this theater (regular, 70mm, Spanish-dubbed) |
| Format marker | `data-print-type-name="Imax 70mm"` |
| Sold out | `<p class="off soldOut" aria-disabled="true">` |
| Available | `<a href="/TicketSeatMap/?TheaterId=207&ShowtimeId=…&CinemarkMovieId=104867&Showtime=…">` |
| Daily 70mm schedule | 7:45am, 11:30am, 3:15pm, 7:00pm, 10:45pm, 2:30am (America/Chicago) |

If Cinemark changes their markup, `assert_page_sane()` will start raising
(caught, counted as a failure, alerts after 3 in a row) rather than silently
reporting "nothing new" forever — check the failure alert body for the error,
then re-derive the selectors above with `curl` against a live theater URL.

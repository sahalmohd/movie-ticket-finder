#!/usr/bin/env python3
"""Watch Cinemark Dallas XD and IMAX for new on-sale days of The Odyssey IMAX 70MM.

Polls the theater's server-rendered showtime page (no JS, no auth) and pushes
an ntfy.sh notification the moment the on-sale window extends to a new day.
State (the current on-sale "frontier" date) is persisted in state.json.
"""
import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

THEATER_URL = "https://www.cinemark.com/theatres/tx-dallas/cinemark-dallas-xd-and-imax"
THEATER_PAGE_URL = "https://www.cinemark.com/theatres/tx-dallas/cinemark-dallas-xd-and-imax"
MOVIE_ID = "104867"  # The Odyssey IMAX 70MM
FORMAT_LABEL = "Imax 70mm"
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.cinemark.com/movies/the-odyssey-imax-70mm",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Upgrade-Insecure-Requests": "1",
}

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
MAX_FORWARD_WALK_DAYS = 21
FAILURE_ALERT_THRESHOLD = 3
HEARTBEAT_INTERVAL_DAYS = 7


def date_str(d):
    return d.strftime("%Y-%m-%d")


def parse_date(s):
    return datetime.datetime.strptime(s, "%Y-%m-%d").date()


def fetch(date, attempts=3, timeout=30):
    url = f"{THEATER_URL}?showDate={date_str(date)}"
    last_err = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers=REQUEST_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            if i < attempts - 1:
                time.sleep(2 ** i)
    raise RuntimeError(f"failed to fetch {url}: {last_err}")


def assert_page_sane(html):
    if "showDate=" not in html:
        raise RuntimeError("page missing showDate= date picker — likely blocked or errored")
    if '<h3 id="' not in html:
        raise RuntimeError("page missing any <h3 id=...> movie block — likely blocked or errored")


def parse_70mm(html):
    """Return list of {time, sold_out, url} for the 70mm block, [] if absent."""
    marker = f'<h3 id="{MOVIE_ID}">'
    start = html.find(marker)
    if start < 0:
        return []
    next_block = html.find("showtimeMovieBlock", start)
    segment = html[start:next_block] if next_block > 0 else html[start:]

    shows = []
    for m in re.finditer(
        r'data-print-type-name="' + re.escape(FORMAT_LABEL) + r'"(.{0,600}?)</div>',
        segment,
        re.S,
    ):
        body = m.group(1)
        time_m = re.search(r"(\d{1,2}:\d{2}\s*[apAP][mM])", body)
        if not time_m:
            continue
        sold_out = "soldOut" in body
        href_m = re.search(r'href="([^"]{0,160})"', body)
        url = None
        if href_m:
            url = href_m.group(1).replace("&amp;", "&")
            if url.startswith("/"):
                url = "https://www.cinemark.com" + url
        shows.append({
            "time": time_m.group(1).strip(),
            "sold_out": sold_out,
            "url": url,
        })
    return shows


def load_state():
    if not os.path.exists(STATE_PATH):
        return {
            "frontier": None,
            "consecutive_failures": 0,
            "last_notified_at": None,
            "last_check_at": None,
        }
    with open(STATE_PATH) as f:
        return json.load(f)


def save_state(state, dry_run):
    if dry_run:
        print(f"[dry-run] would write state: {json.dumps(state, indent=2)}")
        return
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def notify(title, body, priority="default", tags="clapper", click=None, dry_run=False):
    topic = os.environ.get("NTFY_TOPIC")
    if dry_run or not topic:
        print(f"[notify{'(dry-run)' if dry_run else ''}] title={title!r} priority={priority}")
        print(body)
        return
    headers = {
        "Title": title.encode("ascii", errors="ignore"),
        "Priority": priority,
        "Tags": tags,
    }
    if click:
        headers["Click"] = click
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=body.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"warning: failed to send ntfy notification: {e}", file=sys.stderr)


def format_shows(shows):
    lines = []
    for s in shows:
        if s["sold_out"]:
            lines.append(f"  {s['time']} — sold out")
        else:
            lines.append(f"  {s['time']} — {s['url']}")
    return "\n".join(lines)


def bootstrap(today, dry_run):
    """No prior state: walk forward to find the current frontier, notify once, don't treat it as new."""
    print("bootstrapping: no prior frontier, scanning forward from today...")
    frontier = None
    d = today
    for _ in range(MAX_FORWARD_WALK_DAYS + 1):
        html = fetch(d)
        assert_page_sane(html)
        shows = parse_70mm(html)
        if not shows:
            break
        frontier = d
        d += datetime.timedelta(days=1)

    if frontier is None:
        print("no 70mm showtimes found at all during bootstrap — leaving frontier unset")
        return {
            "frontier": None,
            "consecutive_failures": 0,
            "last_notified_at": None,
            "last_check_at": date_str(today),
        }

    notify(
        "🎬 Odyssey 70mm watcher started",
        f"Currently on sale through {date_str(frontier)}. "
        f"You'll get a push the moment a new day opens.",
        priority="low",
        dry_run=dry_run,
    )
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return {
        "frontier": date_str(frontier),
        "consecutive_failures": 0,
        "last_notified_at": now,
        "last_check_at": now,
    }


def check(state, today, dry_run):
    frontier = parse_date(state["frontier"])
    probe_date = frontier + datetime.timedelta(days=1)

    html = fetch(probe_date)
    assert_page_sane(html)
    shows = parse_70mm(html)

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    state["last_check_at"] = now
    state["consecutive_failures"] = 0

    if not shows:
        print(f"{date_str(probe_date)}: no 70mm showtimes yet, nothing new")
        return state

    # New day(s) opened — walk forward to find the new frontier.
    new_days = {date_str(probe_date): shows}
    d = probe_date + datetime.timedelta(days=1)
    for _ in range(MAX_FORWARD_WALK_DAYS):
        html = fetch(d)
        assert_page_sane(html)
        s = parse_70mm(html)
        if not s:
            break
        new_days[date_str(d)] = s
        d += datetime.timedelta(days=1)

    new_frontier = max(new_days.keys())
    n = len(new_days)
    title = f"🎬 Odyssey 70mm — {n} new day{'s' if n != 1 else ''} on sale"
    body_parts = []
    for day in sorted(new_days.keys()):
        body_parts.append(f"{day}:")
        body_parts.append(format_shows(new_days[day]))
    body = "\n".join(body_parts)
    first_day_url = f"{THEATER_PAGE_URL}?showDate={sorted(new_days.keys())[0]}"

    print(title)
    print(body)
    notify(title, body, priority="urgent", click=first_day_url, dry_run=dry_run)

    state["frontier"] = new_frontier
    state["last_notified_at"] = now
    return state


def heartbeat_if_stale(state, dry_run):
    if not state.get("last_notified_at") or not state.get("frontier"):
        return state
    last = datetime.datetime.fromisoformat(state["last_notified_at"])
    if datetime.datetime.now(datetime.timezone.utc) - last < datetime.timedelta(days=HEARTBEAT_INTERVAL_DAYS):
        return state
    notify(
        "🎬 Odyssey 70mm watcher — still watching",
        f"Still on sale through {state['frontier']}. No new days yet.",
        priority="low",
        dry_run=dry_run,
    )
    state["last_notified_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return state


def run_self_test():
    fixtures_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
    cases = [
        ("d-2026-08-20.html", 6, 5),
        ("d-2026-07-30.html", 6, 1),
        ("d-2026-08-21.html", 0, 0),
    ]
    failures = 0
    for fname, expect_total, expect_available in cases:
        path = os.path.join(fixtures_dir, fname)
        with open(path, encoding="utf-8", errors="replace") as f:
            html = f.read()
        assert_page_sane(html)
        shows = parse_70mm(html)
        available = sum(1 for s in shows if not s["sold_out"])
        ok = len(shows) == expect_total and available == expect_available
        status = "OK" if ok else "FAIL"
        print(f"[{status}] {fname}: total={len(shows)} (expect {expect_total}), "
              f"available={available} (expect {expect_available})")
        if not ok:
            failures += 1

    # sanity guard: garbage page should raise
    try:
        assert_page_sane("<html><body>blocked</body></html>")
        print("[FAIL] assert_page_sane did not raise on garbage page")
        failures += 1
    except RuntimeError:
        print("[OK] assert_page_sane raises on garbage page")

    if failures:
        print(f"\n{failures} self-test failure(s)")
        sys.exit(1)
    print("\nall self-tests passed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="don't send notifications or write state")
    parser.add_argument("--self-test", action="store_true", help="run parser tests against fixtures/ and exit")
    args = parser.parse_args()

    if args.self_test:
        run_self_test()
        return

    today = datetime.date.today()
    state = load_state()

    try:
        if not state.get("frontier"):
            state = bootstrap(today, args.dry_run)
        else:
            state = check(state, today, args.dry_run)
            state = heartbeat_if_stale(state, args.dry_run)
    except Exception as e:
        state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
        state["last_check_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        print(f"error: {e}", file=sys.stderr)
        if state["consecutive_failures"] >= FAILURE_ALERT_THRESHOLD:
            notify(
                "⚠️ Odyssey 70mm watcher is failing",
                f"{state['consecutive_failures']} consecutive failures. Last error: {e}",
                priority="low",
                dry_run=args.dry_run,
            )
            state["consecutive_failures"] = 0
        save_state(state, args.dry_run)
        sys.exit(1)

    save_state(state, args.dry_run)


if __name__ == "__main__":
    main()

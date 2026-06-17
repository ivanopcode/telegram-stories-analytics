# Telegram Stories Analytics

`tg-stories` exports your own Telegram Stories through Telethon, fetches viewer
lists, computes engagement analytics, and renders local JSON, Markdown, and
HTML reports.

The CLI is packaged as a normal Python console script and works on macOS,
Linux, and Windows. Authentication is stored in the system keyring. On macOS it
can also reuse credentials created by the local `telegram-telethon` skill.

## Install

For normal use:

```bash
pipx install telegram-stories-analytics
```

For local development:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## Telegram API Credentials

Create `api_id` and `api_hash` at <https://my.telegram.org> under API
Development Tools.

Then login once:

```bash
tg-stories auth login --profile personal
```

Check the session:

```bash
tg-stories auth status --profile personal
```

On macOS, if you already use the `telegram-telethon` skill, this tool can reuse
that Keychain session:

```bash
tg-stories auth status --profile ivanopcode --auth-source telegram-telethon
```

## Export Stories

Full export:

```bash
tg-stories export --profile personal --progress --open
```

This writes:

```text
exports/my-telegram-stories-full.json
exports/my-telegram-stories-analysis.json
exports/my-telegram-stories-analysis.md
exports/my-telegram-stories-analysis.html
```

The full JSON contains each story, Telegram story link when available, metadata,
counts, and a nested `viewers` array with viewer identity, view date, and
reaction.

## Rebuild Reports

If you already have a full export, recompute analytics without calling Telegram:

```bash
tg-stories analyze exports/my-telegram-stories-full.json --open
```

Render or open HTML from an analysis JSON:

```bash
tg-stories html exports/my-telegram-stories-analysis.json --open
tg-stories open exports/my-telegram-stories-analysis.json
```

## Analytics

The generated analysis includes:

- all-time regularity and `>=80%` viewers;
- recent windows, by default 30, 90, and 180 days;
- sliding-window regularity;
- fastest regular viewers by median view latency;
- story-level speed buckets: 5m, 15m, 1h, 6h, and 24h;
- top viewers by absolute reactions;
- top viewers by reaction rate;
- lapsed viewers, new recent viewers, and declining viewers;
- contact, mutual contact, non-contact, and premium segments.

Important definitions:

- `view_rate = viewed_story_count / story_count`
- `reaction_rate_per_viewed_story = reaction_count / viewed_story_count`
- `view_latency_seconds = viewer view_date - story date`

## Environment Auth

For CI-like or temporary usage, credentials can be provided through environment
variables:

```bash
export TG_STORIES_API_ID=12345
export TG_STORIES_API_HASH=...
export TG_STORIES_SESSION=...
tg-stories export --auth-source env
```

Do not commit exported reports. Viewer data is personal data.


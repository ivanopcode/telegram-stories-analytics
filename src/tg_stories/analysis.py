"""Analytics and Markdown report generation for Telegram Stories exports."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AnalysisConfig:
    top_limit: int = 50
    regularity_threshold: float = 0.80
    sliding_window_fraction: float = 0.10
    sliding_window_min: int = 10
    recent_days: list[int] = field(default_factory=lambda: [30, 90, 180])
    min_reaction_rate_views: int = 20
    min_fast_regular_views: int = 50
    lapsed_prior_min_views: int = 20
    new_viewer_min_views: int = 3


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    data = sorted(values)
    if len(data) == 1:
        return data[0]
    k = (len(data) - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return data[int(k)]
    return data[lo] * (hi - k) + data[hi] * (k - lo)


def round_rate(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def human_duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    minutes %= 60
    if hours < 48:
        return f"{hours}h {minutes}m"
    days = hours // 24
    hours %= 24
    return f"{days}d {hours}h"


def public_user(user: dict[str, Any] | None) -> dict[str, Any] | None:
    if not user:
        return None
    return {
        "id": user.get("id"),
        "name": user.get("name"),
        "username": user.get("username"),
        "is_contact": bool(user.get("is_contact")),
        "is_mutual_contact": bool(user.get("is_mutual_contact")),
        "is_premium": bool(user.get("is_premium")),
        "is_bot": bool(user.get("is_bot")),
        "is_verified": bool(user.get("is_verified")),
    }


def user_label(user: dict[str, Any] | None) -> str:
    if not user:
        return "unknown"
    name = user.get("name") or user.get("username") or f"user_{user.get('id')}"
    username = user.get("username")
    return f"{name} (@{username})" if username and username not in str(name) else str(name)


def compute_streak(indices: set[int]) -> dict[str, int | None]:
    if not indices:
        return {"longest_streak": 0, "current_streak": 0, "max_gap": None}
    ordered = sorted(indices)
    longest = 1
    current = 1
    max_gap = 0
    for previous, current_index in zip(ordered, ordered[1:]):
        gap = current_index - previous - 1
        max_gap = max(max_gap, gap)
        if current_index == previous + 1:
            current += 1
        else:
            longest = max(longest, current)
            current = 1
    longest = max(longest, current)
    return {"longest_streak": longest, "current_streak": current, "max_gap": max_gap}


def rolling_stats(indices: set[int], total: int, window_size: int, threshold: float) -> dict[str, Any]:
    if total == 0:
        return {
            "window_size": 0,
            "total_windows": 0,
            "min_window_rate": None,
            "avg_window_rate": None,
            "windows_meeting_threshold": 0,
        }
    window_size = min(total, max(1, window_size))
    hits = [0] * total
    for index in indices:
        if 0 <= index < total:
            hits[index] = 1

    if total <= window_size:
        rate = sum(hits) / total
        return {
            "window_size": total,
            "total_windows": 1,
            "min_window_rate": rate,
            "avg_window_rate": rate,
            "windows_meeting_threshold": 1 if rate >= threshold else 0,
        }

    rates = []
    current = sum(hits[:window_size])
    rates.append(current / window_size)
    for index in range(window_size, total):
        current += hits[index] - hits[index - window_size]
        rates.append(current / window_size)
    return {
        "window_size": window_size,
        "total_windows": len(rates),
        "min_window_rate": min(rates),
        "avg_window_rate": sum(rates) / len(rates),
        "windows_meeting_threshold": sum(1 for rate in rates if rate >= threshold),
    }


def top_rows(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return rows[:limit]


def build_analysis(payload: dict[str, Any], config: AnalysisConfig) -> dict[str, Any]:
    stories = payload.get("stories", [])
    for index, story in enumerate(stories):
        story["_index"] = index
        story["_date"] = parse_dt(story.get("date"))

    story_dates = [story["_date"] for story in stories if story.get("_date")]
    first_story = min(story_dates) if story_dates else None
    last_story = max(story_dates) if story_dates else None
    total_stories = len(stories)

    users: dict[int, dict[str, Any]] = {}
    viewed_indices: dict[int, set[int]] = defaultdict(set)
    reaction_by_user: dict[int, Counter[str]] = defaultdict(Counter)
    view_dates: dict[int, list[datetime]] = defaultdict(list)
    latencies: dict[int, list[float]] = defaultdict(list)
    all_latencies: list[float] = []
    view_events = 0
    reaction_events = 0
    segment_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"unique_user_ids": set(), "view_events": 0, "reaction_events": 0}
    )
    story_speed = []

    for story in stories:
        story_date = story.get("_date")
        story_latencies: list[float] = []
        story_reactions = 0
        story_unique_viewers = 0

        for viewer in story.get("viewers") or []:
            user = viewer.get("user") or {}
            user_id = user.get("id")
            if user_id is None:
                continue

            users.setdefault(user_id, public_user(user) or {})
            viewed_indices[user_id].add(story["_index"])
            story_unique_viewers += 1
            view_events += 1

            view_dt = parse_dt(viewer.get("view_date"))
            if view_dt:
                view_dates[user_id].append(view_dt)
            if story_date and view_dt:
                latency = (view_dt - story_date).total_seconds()
                if latency >= 0:
                    latencies[user_id].append(latency)
                    story_latencies.append(latency)
                    all_latencies.append(latency)

            reaction = viewer.get("reaction")
            if reaction:
                reaction_events += 1
                story_reactions += 1
                reaction_by_user[user_id][reaction] += 1

            labels = ["contacts" if user.get("is_contact") else "non_contacts"]
            if user.get("is_mutual_contact"):
                labels.append("mutual_contacts")
            if user.get("is_premium"):
                labels.append("premium")
            if user.get("is_bot"):
                labels.append("bots")
            for label in labels:
                segment_stats[label]["unique_user_ids"].add(user_id)
                segment_stats[label]["view_events"] += 1
                if reaction:
                    segment_stats[label]["reaction_events"] += 1

        story_speed.append(
            {
                "story_id": story.get("id"),
                "date": story.get("date"),
                "date_local": story.get("date_local"),
                "link": story.get("link"),
                "viewers_count": story_unique_viewers,
                "reaction_count": story_reactions,
                "views_within_5m": sum(1 for item in story_latencies if item <= 300),
                "views_within_15m": sum(1 for item in story_latencies if item <= 900),
                "views_within_1h": sum(1 for item in story_latencies if item <= 3600),
                "views_within_6h": sum(1 for item in story_latencies if item <= 6 * 3600),
                "views_within_24h": sum(1 for item in story_latencies if item <= 24 * 3600),
                "median_view_latency_seconds": percentile(story_latencies, 0.5),
                "p90_view_latency_seconds": percentile(story_latencies, 0.9),
            }
        )

    window_size = (
        min(total_stories, max(config.sliding_window_min, math.ceil(total_stories * config.sliding_window_fraction)))
        if total_stories
        else 0
    )

    user_rows = build_user_rows(
        config=config,
        users=users,
        viewed_indices=viewed_indices,
        reaction_by_user=reaction_by_user,
        view_dates=view_dates,
        latencies=latencies,
        total_stories=total_stories,
        window_size=window_size,
    )
    regularity = sorted(
        user_rows,
        key=lambda row: (
            row["view_rate"] or 0,
            row["viewed_story_count"],
            row["reaction_count"],
            row["rolling"]["avg_window_rate"] or 0,
        ),
        reverse=True,
    )
    reactions_absolute = sorted(
        [row for row in user_rows if row["reaction_count"] > 0],
        key=lambda row: (row["reaction_count"], row["viewed_story_count"]),
        reverse=True,
    )
    reactions_rate = sorted(
        [
            row
            for row in user_rows
            if row["viewed_story_count"] >= config.min_reaction_rate_views and row["reaction_count"] > 0
        ],
        key=lambda row: (
            row["reaction_rate_per_viewed_story"] or 0,
            row["reaction_count"],
            row["viewed_story_count"],
        ),
        reverse=True,
    )
    fast_regulars = sorted(
        [
            row
            for row in user_rows
            if row["viewed_story_count"] >= config.min_fast_regular_views
            and row["median_view_latency_seconds"] is not None
        ],
        key=lambda row: (row["median_view_latency_seconds"], -row["viewed_story_count"]),
    )

    story_speed_fast = sorted(
        story_speed,
        key=lambda row: (row["views_within_1h"], row["views_within_6h"], row["viewers_count"]),
        reverse=True,
    )
    story_speed_slow = sorted(
        [row for row in story_speed if row["viewers_count"] >= 10],
        key=lambda row: row["median_view_latency_seconds"]
        if row["median_view_latency_seconds"] is not None
        else float("-inf"),
        reverse=True,
    )

    analysis = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "definitions": {
            "regularity": "viewed_story_count / story_count in the selected window",
            "reaction_rate_per_viewed_story": "reaction_count / viewed_story_count",
            "view_latency_seconds": "viewer view_date - story date; negative/missing values are ignored",
            "lapsed_viewers": (
                f"viewed at least {config.lapsed_prior_min_views} stories before the last 90 days "
                "and 0 in the last 90 days"
            ),
            "new_recent_viewers": (
                f"first seen in last 90 days and viewed at least {config.new_viewer_min_views} stories"
            ),
        },
        "summary": {
            "story_count": total_stories,
            "first_story_date": first_story.isoformat() if first_story else None,
            "last_story_date": last_story.isoformat() if last_story else None,
            "unique_viewers": len(users),
            "view_events": view_events,
            "reaction_events": reaction_events,
            "overall_reaction_rate_per_view_event": round_rate(reaction_events / view_events if view_events else 0),
            "median_view_latency_seconds": percentile(all_latencies, 0.5),
            "p75_view_latency_seconds": percentile(all_latencies, 0.75),
            "p90_view_latency_seconds": percentile(all_latencies, 0.9),
            "views_within_1h_rate": round_rate(
                sum(1 for item in all_latencies if item <= 3600) / len(all_latencies)
                if all_latencies
                else 0
            ),
            "views_within_6h_rate": round_rate(
                sum(1 for item in all_latencies if item <= 6 * 3600) / len(all_latencies)
                if all_latencies
                else 0
            ),
            "views_within_24h_rate": round_rate(
                sum(1 for item in all_latencies if item <= 24 * 3600) / len(all_latencies)
                if all_latencies
                else 0
            ),
        },
        "regularity": {
            "threshold": config.regularity_threshold,
            "sliding_window": {
                "window_size_stories": window_size,
                "window_fraction": config.sliding_window_fraction,
                "window_minimum": config.sliding_window_min,
            },
            "all_time_top": top_rows(regularity, config.top_limit),
            "all_time_80_pct": [
                row for row in regularity if (row["view_rate"] or 0) >= config.regularity_threshold
            ],
            "recent_windows": build_recent_windows(
                config, stories, viewed_indices, users, reaction_by_user, last_story, total_stories
            ),
        },
        "speed": {
            "top_fast_regular_viewers": top_rows(fast_regulars, config.top_limit),
            "top_stories_by_views_within_1h": top_rows(story_speed_fast, config.top_limit),
            "slowest_stories_by_median_view_latency": top_rows(story_speed_slow, config.top_limit),
        },
        "churn": build_churn(config, stories, viewed_indices, users, view_dates, reaction_by_user, last_story, total_stories),
        "reactions": {
            "top_by_absolute_count": top_rows(reactions_absolute, config.top_limit),
            "top_by_rate_min_views": top_rows(reactions_rate, config.top_limit),
        },
        "segments": build_segments(segment_stats),
    }

    for story in stories:
        story.pop("_index", None)
        story.pop("_date", None)
    return analysis


def build_user_rows(
    *,
    config: AnalysisConfig,
    users: dict[int, dict[str, Any]],
    viewed_indices: dict[int, set[int]],
    reaction_by_user: dict[int, Counter[str]],
    view_dates: dict[int, list[datetime]],
    latencies: dict[int, list[float]],
    total_stories: int,
    window_size: int,
) -> list[dict[str, Any]]:
    rows = []
    for user_id, indices in viewed_indices.items():
        viewed = len(indices)
        reactions = reaction_by_user.get(user_id, Counter())
        reaction_count = sum(reactions.values())
        user_latencies = latencies.get(user_id, [])
        row = {
            "user": users.get(user_id),
            "viewed_story_count": viewed,
            "total_story_count": total_stories,
            "view_rate": round_rate(viewed / total_stories if total_stories else 0),
            "first_view_date": min(view_dates[user_id]).isoformat() if view_dates[user_id] else None,
            "last_view_date": max(view_dates[user_id]).isoformat() if view_dates[user_id] else None,
            "reaction_count": reaction_count,
            "reaction_rate_per_viewed_story": round_rate(reaction_count / viewed if viewed else 0),
            "reaction_breakdown": dict(reactions),
            "median_view_latency_seconds": percentile(user_latencies, 0.5),
            "p75_view_latency_seconds": percentile(user_latencies, 0.75),
            "p90_view_latency_seconds": percentile(user_latencies, 0.9),
            "views_within_1h": sum(1 for item in user_latencies if item <= 3600),
            "views_within_6h": sum(1 for item in user_latencies if item <= 6 * 3600),
            "views_within_24h": sum(1 for item in user_latencies if item <= 24 * 3600),
            "within_1h_rate": round_rate(
                sum(1 for item in user_latencies if item <= 3600) / len(user_latencies)
                if user_latencies
                else 0
            ),
            "within_6h_rate": round_rate(
                sum(1 for item in user_latencies if item <= 6 * 3600) / len(user_latencies)
                if user_latencies
                else 0
            ),
            "within_24h_rate": round_rate(
                sum(1 for item in user_latencies if item <= 24 * 3600) / len(user_latencies)
                if user_latencies
                else 0
            ),
            "rolling": rolling_stats(indices, total_stories, window_size, config.regularity_threshold),
            **compute_streak(indices),
        }
        rows.append(row)
    return rows


def build_recent_windows(
    config: AnalysisConfig,
    stories: list[dict[str, Any]],
    viewed_indices: dict[int, set[int]],
    users: dict[int, dict[str, Any]],
    reaction_by_user: dict[int, Counter[str]],
    last_story: datetime | None,
    total_stories: int,
) -> dict[str, Any]:
    if last_story is None:
        return {}
    windows: dict[str, Any] = {}
    for days in config.recent_days:
        start = last_story - timedelta(days=days)
        indices = {story["_index"] for story in stories if story.get("_date") and story["_date"] >= start}
        total = len(indices)
        rows = []
        for user_id, user_indices in viewed_indices.items():
            hits = len(user_indices & indices)
            if hits == 0:
                continue
            reactions = reaction_by_user.get(user_id, Counter())
            rows.append(
                {
                    "user": users.get(user_id),
                    "viewed_story_count": hits,
                    "total_story_count": total,
                    "view_rate": round_rate(hits / total if total else 0),
                    "all_time_viewed_story_count": len(user_indices),
                    "all_time_view_rate": round_rate(len(user_indices) / total_stories if total_stories else 0),
                    "all_time_reaction_count": sum(reactions.values()),
                }
            )
        rows.sort(
            key=lambda row: (row["view_rate"] or 0, row["viewed_story_count"], row["all_time_reaction_count"]),
            reverse=True,
        )
        windows[f"last_{days}_days"] = {
            "start_date": start.isoformat(),
            "end_date": last_story.isoformat(),
            "story_count": total,
            "unique_viewers": len(rows),
            "regular_threshold_count": sum(
                1 for row in rows if (row["view_rate"] or 0) >= config.regularity_threshold
            ),
            "top_by_regularity": top_rows(rows, config.top_limit),
        }
    return windows


def build_churn(
    config: AnalysisConfig,
    stories: list[dict[str, Any]],
    viewed_indices: dict[int, set[int]],
    users: dict[int, dict[str, Any]],
    view_dates: dict[int, list[datetime]],
    reaction_by_user: dict[int, Counter[str]],
    last_story: datetime | None,
    total_stories: int,
) -> dict[str, list[dict[str, Any]]]:
    if last_story is None:
        return {"lapsed_viewers": [], "new_recent_viewers": [], "declining_recent_30d": []}

    last_90_start = last_story - timedelta(days=90)
    last_30_start = last_story - timedelta(days=30)
    recent_90_indices = {story["_index"] for story in stories if story.get("_date") and story["_date"] >= last_90_start}
    prior_90_indices = {story["_index"] for story in stories if story.get("_date") and story["_date"] < last_90_start}
    recent_30_indices = {story["_index"] for story in stories if story.get("_date") and story["_date"] >= last_30_start}

    lapsed = []
    new_recent = []
    declining = []
    for user_id, indices in viewed_indices.items():
        prior_count = len(indices & prior_90_indices)
        recent90_count = len(indices & recent_90_indices)
        recent30_count = len(indices & recent_30_indices)
        first_story_date = stories[min(indices)]["_date"] if indices else None

        if prior_count >= config.lapsed_prior_min_views and recent90_count == 0:
            lapsed.append(
                {
                    "user": users.get(user_id),
                    "prior_viewed_story_count": prior_count,
                    "recent_90d_viewed_story_count": recent90_count,
                    "last_view_date": max(view_dates[user_id]).isoformat() if view_dates[user_id] else None,
                    "all_time_viewed_story_count": len(indices),
                    "all_time_view_rate": round_rate(len(indices) / total_stories if total_stories else 0),
                }
            )

        if first_story_date and first_story_date >= last_90_start and len(indices) >= config.new_viewer_min_views:
            new_recent.append(
                {
                    "user": users.get(user_id),
                    "viewed_story_count": len(indices),
                    "first_seen_story_date": first_story_date.isoformat(),
                    "last_view_date": max(view_dates[user_id]).isoformat() if view_dates[user_id] else None,
                    "reaction_count": sum(reaction_by_user.get(user_id, Counter()).values()),
                }
            )

        prior_rate = prior_count / len(prior_90_indices) if prior_90_indices else 0
        recent30_rate = recent30_count / len(recent_30_indices) if recent_30_indices else 0
        if prior_count >= config.lapsed_prior_min_views and prior_rate - recent30_rate >= 0.4:
            declining.append(
                {
                    "user": users.get(user_id),
                    "prior_rate": round_rate(prior_rate),
                    "recent_30d_rate": round_rate(recent30_rate),
                    "delta": round_rate(recent30_rate - prior_rate),
                    "prior_viewed_story_count": prior_count,
                    "recent_30d_viewed_story_count": recent30_count,
                }
            )

    lapsed.sort(key=lambda row: (row["prior_viewed_story_count"], row["all_time_view_rate"] or 0), reverse=True)
    new_recent.sort(key=lambda row: (row["viewed_story_count"], row["reaction_count"]), reverse=True)
    declining.sort(key=lambda row: (row["delta"] or 0, row["prior_rate"] or 0))
    return {
        "lapsed_viewers": top_rows(lapsed, config.top_limit),
        "new_recent_viewers": top_rows(new_recent, config.top_limit),
        "declining_recent_30d": top_rows(declining, config.top_limit),
    }


def build_segments(segment_stats: dict[str, dict[str, Any]]) -> dict[str, Any]:
    segments = {}
    for label, stats in sorted(segment_stats.items()):
        unique_count = len(stats["unique_user_ids"])
        view_events = stats["view_events"]
        reaction_events = stats["reaction_events"]
        segments[label] = {
            "unique_viewers": unique_count,
            "view_events": view_events,
            "reaction_events": reaction_events,
            "reaction_rate_per_view_event": round_rate(reaction_events / view_events if view_events else 0),
            "avg_views_per_unique_viewer": round_rate(view_events / unique_count if unique_count else 0),
        }
    return segments


def write_analysis_json(path: Path, analysis: dict[str, Any], source_file: Path | None = None) -> None:
    payload = {**analysis}
    if source_file is not None:
        payload["source_file"] = str(source_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_markdown(analysis: dict[str, Any], source_file: Path | None, config: AnalysisConfig) -> str:
    summary = analysis["summary"]
    lines = [
        "# Telegram Stories Analysis",
        "",
        f"Source: `{source_file}`" if source_file else "Source: in-memory export",
        f"Generated: `{analysis['generated_at']}`",
        "",
        "## Summary",
        f"- Stories: {summary['story_count']}",
        f"- Range: {summary['first_story_date']} - {summary['last_story_date']}",
        f"- Unique viewers: {summary['unique_viewers']}",
        f"- View events: {summary['view_events']}",
        (
            f"- Reaction events: {summary['reaction_events']} "
            f"({(summary['overall_reaction_rate_per_view_event'] or 0) * 100:.1f}% of view events)"
        ),
        (
            f"- Median view latency: {human_duration(summary['median_view_latency_seconds'])}; "
            f"p75 {human_duration(summary['p75_view_latency_seconds'])}; "
            f"p90 {human_duration(summary['p90_view_latency_seconds'])}"
        ),
        (
            f"- Views within 1h/6h/24h: {(summary['views_within_1h_rate'] or 0) * 100:.1f}% / "
            f"{(summary['views_within_6h_rate'] or 0) * 100:.1f}% / "
            f"{(summary['views_within_24h_rate'] or 0) * 100:.1f}%"
        ),
        "",
        "## All-Time Regularity",
    ]

    for index, row in enumerate(analysis["regularity"]["all_time_top"][:15], start=1):
        lines.append(f"{index}. {user_metric_line(row)}")

    for key, window in analysis["regularity"]["recent_windows"].items():
        lines.extend(
            [
                "",
                f"## {key.replace('_', ' ').title()}",
                (
                    f"- Stories: {window['story_count']}; unique viewers: {window['unique_viewers']}; "
                    f">={config.regularity_threshold * 100:.0f}% regulars: {window['regular_threshold_count']}"
                ),
            ]
        )
        for index, row in enumerate(window["top_by_regularity"][:10], start=1):
            lines.append(f"{index}. {user_metric_line(row)}")

    lines.extend(["", "## Fast Regular Viewers"])
    for index, row in enumerate(analysis["speed"]["top_fast_regular_viewers"][:15], start=1):
        lines.append(
            f"{index}. {user_metric_line(row)} | median {human_duration(row.get('median_view_latency_seconds'))} "
            f"| <=1h {(row.get('within_1h_rate') or 0) * 100:.1f}%"
        )

    lines.extend(["", "## Reactions", "Top absolute reaction count:"])
    for index, row in enumerate(analysis["reactions"]["top_by_absolute_count"][:15], start=1):
        lines.append(
            f"{index}. {user_label(row['user'])} | reactions {row['reaction_count']} | "
            f"viewed {row['viewed_story_count']} | rate {(row['reaction_rate_per_viewed_story'] or 0) * 100:.1f}%"
        )

    lines.extend(["", f"Top reaction rate, min {config.min_reaction_rate_views} viewed stories:"])
    for index, row in enumerate(analysis["reactions"]["top_by_rate_min_views"][:15], start=1):
        lines.append(
            f"{index}. {user_label(row['user'])} | rate {(row['reaction_rate_per_viewed_story'] or 0) * 100:.1f}% | "
            f"reactions {row['reaction_count']} | viewed {row['viewed_story_count']}"
        )

    lines.extend(["", "## Churn", "Lapsed viewers:"])
    for index, row in enumerate(analysis["churn"]["lapsed_viewers"][:10], start=1):
        lines.append(
            f"{index}. {user_label(row['user'])} | prior views {row['prior_viewed_story_count']} | "
            f"last view {row['last_view_date']}"
        )

    lines.extend(["", "New recent viewers:"])
    for index, row in enumerate(analysis["churn"]["new_recent_viewers"][:10], start=1):
        lines.append(
            f"{index}. {user_label(row['user'])} | views {row['viewed_story_count']} | "
            f"first seen {row['first_seen_story_date']}"
        )

    lines.extend(["", "## Segments"])
    for label, stats in analysis["segments"].items():
        lines.append(
            f"- {label}: unique {stats['unique_viewers']}, view events {stats['view_events']}, "
            f"reactions {stats['reaction_events']}, reaction/view {(stats['reaction_rate_per_view_event'] or 0) * 100:.1f}%"
        )

    lines.extend(["", "## Notes"])
    for key, value in analysis["definitions"].items():
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def user_metric_line(row: dict[str, Any]) -> str:
    rate = row.get("view_rate")
    parts = [
        user_label(row.get("user")),
        f"{row.get('viewed_story_count')}/{row.get('total_story_count')}",
        f"{rate * 100:.1f}%" if rate is not None else "",
    ]
    if "reaction_count" in row:
        parts.append(f"reactions {row.get('reaction_count', 0)}")
    return " | ".join(parts)


def write_markdown(path: Path, analysis: dict[str, Any], source_file: Path | None, config: AnalysisConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(analysis, source_file, config) + "\n", encoding="utf-8")

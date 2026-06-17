"""Telegram Stories export built on Telethon raw API."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import telethon
from telethon import functions, types
from telethon.errors import FloodWaitError

from .auth import Credentials


@dataclass(frozen=True)
class ExportConfig:
    archive_page_limit: int = 100
    view_page_limit: int = 100
    limit_stories: int | None = None
    skip_links: bool = False
    skip_viewers: bool = False
    progress: bool = False


def as_iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def as_local_iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.astimezone().isoformat()


def reaction_label(reaction: Any) -> str | None:
    if reaction is None:
        return None
    emoticon = getattr(reaction, "emoticon", None)
    if emoticon:
        return str(emoticon)
    if getattr(reaction, "document_id", None):
        return "custom-emoji"
    return type(reaction).__name__


def reaction_counts(story_views: Any) -> list[dict[str, Any]]:
    reactions = getattr(story_views, "reactions", None) or []
    return [
        {
            "reaction": reaction_label(getattr(item, "reaction", None)),
            "count": getattr(item, "count", None),
        }
        for item in reactions
    ]


def media_summary(story: Any) -> dict[str, Any] | None:
    media = getattr(story, "media", None)
    if media is None:
        return None

    document = getattr(media, "document", None)
    photo = getattr(media, "photo", None)
    file_name = None
    duration = None
    width = None
    height = None

    for attr in getattr(document, "attributes", None) or []:
        if getattr(attr, "file_name", None):
            file_name = attr.file_name
        if getattr(attr, "duration", None) is not None:
            duration = attr.duration
        if getattr(attr, "w", None) is not None:
            width = attr.w
        if getattr(attr, "h", None) is not None:
            height = attr.h

    sizes = getattr(photo, "sizes", None) or []
    if sizes:
        last = sizes[-1]
        width = width or getattr(last, "w", None)
        height = height or getattr(last, "h", None)

    return {
        "type": type(media).__name__,
        "mime_type": getattr(document, "mime_type", None),
        "file_name": file_name,
        "size": getattr(document, "size", None),
        "duration": duration,
        "width": width,
        "height": height,
    }


def user_record(user: Any) -> dict[str, Any] | None:
    if user is None:
        return None
    first = getattr(user, "first_name", None) or ""
    last = getattr(user, "last_name", None) or ""
    full_name = " ".join(part for part in (first, last) if part).strip() or None
    return {
        "id": getattr(user, "id", None),
        "name": full_name,
        "first_name": getattr(user, "first_name", None),
        "last_name": getattr(user, "last_name", None),
        "username": getattr(user, "username", None),
        "is_contact": bool(getattr(user, "contact", False)),
        "is_mutual_contact": bool(getattr(user, "mutual_contact", False)),
        "is_premium": bool(getattr(user, "premium", False)),
        "is_bot": bool(getattr(user, "bot", False)),
        "is_verified": bool(getattr(user, "verified", False)),
    }


def viewer_record(view: Any, users_by_id: dict[int | None, Any]) -> dict[str, Any]:
    user_id = getattr(view, "user_id", None)
    return {
        "user": user_record(users_by_id.get(user_id)),
        "view_date": as_iso(getattr(view, "date", None)),
        "view_date_local": as_local_iso(getattr(view, "date", None)),
        "reaction": reaction_label(getattr(view, "reaction", None)),
        "blocked": bool(getattr(view, "blocked", False)),
        "blocked_my_stories_from": bool(getattr(view, "blocked_my_stories_from", False)),
    }


def story_record(story: Any) -> dict[str, Any]:
    views = getattr(story, "views", None)
    return {
        "id": getattr(story, "id", None),
        "type": type(story).__name__,
        "date": as_iso(getattr(story, "date", None)),
        "date_local": as_local_iso(getattr(story, "date", None)),
        "expire_date": as_iso(getattr(story, "expire_date", None)),
        "expire_date_local": as_local_iso(getattr(story, "expire_date", None)),
        "caption": getattr(story, "caption", None),
        "media": media_summary(story),
        "privacy": {
            "public": bool(getattr(story, "public", False)),
            "close_friends": bool(getattr(story, "close_friends", False)),
            "contacts": bool(getattr(story, "contacts", False)),
            "selected_contacts": bool(getattr(story, "selected_contacts", False)),
            "noforwards": bool(getattr(story, "noforwards", False)),
            "pinned": bool(getattr(story, "pinned", False)),
        },
        "counts_from_story": {
            "views_count": getattr(views, "views_count", None),
            "forwards_count": getattr(views, "forwards_count", None),
            "reactions_count": getattr(views, "reactions_count", None),
            "has_viewers": bool(getattr(views, "has_viewers", False)),
            "recent_viewers": list(getattr(views, "recent_viewers", None) or []),
            "reaction_counts": reaction_counts(views),
        },
        "link": None,
        "link_error": None,
        "viewers_count_reported": None,
        "viewers_count_exported": 0,
        "viewers_next_offset_left": False,
        "viewers": [],
    }


async def call_with_flood_sleep(factory: Any) -> Any:
    while True:
        try:
            return await factory()
        except FloodWaitError as exc:
            await asyncio.sleep(int(getattr(exc, "seconds", 1)) + 1)


async def fetch_active_stories(client: Any, peer: Any) -> list[Any]:
    result = await call_with_flood_sleep(lambda: client(functions.stories.GetPeerStoriesRequest(peer=peer)))
    holder = getattr(result, "stories", None)
    return [story for story in (getattr(holder, "stories", None) or []) if getattr(story, "id", None) is not None]


async def fetch_archive_stories(client: Any, peer: Any, page_limit: int) -> dict[int, Any]:
    by_id: dict[int, Any] = {}
    offset_id = 0
    while True:
        result = await call_with_flood_sleep(
            lambda: client(
                functions.stories.GetStoriesArchiveRequest(peer=peer, offset_id=offset_id, limit=page_limit)
            )
        )
        stories = [story for story in (getattr(result, "stories", None) or []) if getattr(story, "id", None) is not None]
        fresh = []
        for story in stories:
            if story.id not in by_id:
                by_id[story.id] = story
                fresh.append(story)
        if not stories or not fresh:
            break
        next_offset = min(story.id for story in stories)
        if next_offset == offset_id:
            break
        offset_id = next_offset
    return by_id


async def export_story_link(client: Any, peer: Any, story_id: int) -> tuple[str | None, dict[str, Any] | None]:
    try:
        result = await call_with_flood_sleep(
            lambda: client(functions.stories.ExportStoryLinkRequest(peer=peer, id=story_id))
        )
        return getattr(result, "link", None), None
    except Exception as exc:  # Telegram may reject links for unavailable stories.
        return None, {"type": type(exc).__name__, "message": str(exc)}


async def fetch_viewers(client: Any, peer: Any, story_id: int, page_limit: int) -> tuple[int | None, list[dict[str, Any]], bool]:
    offset = ""
    reported = None
    exported: list[dict[str, Any]] = []
    seen_offsets: set[str] = set()

    while True:
        result = await call_with_flood_sleep(
            lambda: client(
                functions.stories.GetStoryViewsListRequest(peer=peer, id=story_id, offset=offset, limit=page_limit)
            )
        )
        if reported is None:
            reported = getattr(result, "count", None)
        users_by_id = {getattr(user, "id", None): user for user in (getattr(result, "users", None) or [])}
        exported.extend(viewer_record(view, users_by_id) for view in (getattr(result, "views", None) or []))

        next_offset = getattr(result, "next_offset", None)
        if not next_offset:
            return reported, exported, False
        if next_offset in seen_offsets:
            return reported, exported, True
        seen_offsets.add(next_offset)
        offset = next_offset


async def export_my_stories(client: Any, credentials: Credentials, config: ExportConfig) -> dict[str, Any]:
    account = await client.get_me()
    peer = types.InputPeerSelf()
    active = await fetch_active_stories(client, peer)
    archive_by_id = await fetch_archive_stories(client, peer, config.archive_page_limit)
    for story in active:
        archive_by_id.setdefault(story.id, story)

    story_items = [story for story in archive_by_id.values() if type(story).__name__ == "StoryItem"]
    story_items.sort(
        key=lambda story: (
            getattr(story, "date", None) or getattr(story, "expire_date", None) or datetime.min.replace(tzinfo=timezone.utc),
            getattr(story, "id", 0),
        )
    )
    if config.limit_stories:
        story_items = story_items[-config.limit_stories :]

    stories = []
    link_errors = 0
    viewer_errors: list[dict[str, Any]] = []
    for index, story in enumerate(story_items, start=1):
        if config.progress:
            print(f"story {index}/{len(story_items)} id={story.id}", file=sys.stderr)
        record = story_record(story)

        if not config.skip_links:
            link, link_error = await export_story_link(client, peer, story.id)
            record["link"] = link
            record["link_error"] = link_error
            if link_error:
                link_errors += 1

        if not config.skip_viewers:
            try:
                reported, viewers, next_left = await fetch_viewers(client, peer, story.id, config.view_page_limit)
                record["viewers_count_reported"] = reported
                record["viewers_count_exported"] = len(viewers)
                record["viewers_next_offset_left"] = next_left
                record["viewers"] = viewers
            except Exception as exc:  # noqa: BLE001 - keep exporting remaining stories.
                error = {"story_id": story.id, "type": type(exc).__name__, "message": str(exc)}
                record["viewers_error"] = error
                viewer_errors.append(error)

        stories.append(record)

    return {
        "schema_version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "profile": credentials.profile,
        "auth_source": credentials.source,
        "account": user_record(account),
        "source": {
            "telethon_version": telethon.__version__,
            "peer": "InputPeerSelf",
            "archive_page_limit": config.archive_page_limit,
            "view_page_limit": config.view_page_limit,
            "skip_links": config.skip_links,
            "skip_viewers": config.skip_viewers,
        },
        "stories": stories,
        "export_warnings": {
            "link_errors": link_errors,
            "viewer_errors": viewer_errors,
            "note": "Telegram may omit deleted/unavailable stories or some viewer identities depending on server-side availability/privacy.",
        },
    }

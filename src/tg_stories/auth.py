"""Authentication helpers for Telegram Stories analytics."""

from __future__ import annotations

import asyncio
import getpass
import json
import os
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import keyring
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession


APP_SERVICE = "telegram-stories-analytics"
TELEGRAM_TELETHON_SERVICE = "telegram-telethon"
PROFILE_ENV = "TG_STORIES_PROFILE"
ENV_API_ID = "TG_STORIES_API_ID"
ENV_API_HASH = "TG_STORIES_API_HASH"
ENV_SESSION = "TG_STORIES_SESSION"
ENV_PHONE = "TG_STORIES_PHONE"


@dataclass(frozen=True)
class Credentials:
    profile: str
    api_id: int
    api_hash: str
    session: str
    phone: str | None = None
    source: str = "unknown"


class AuthError(RuntimeError):
    pass


def resolve_profile(profile: str | None) -> str:
    return profile or os.environ.get(PROFILE_ENV) or "default"


def service_name(profile: str) -> str:
    return f"{APP_SERVICE}:{profile}"


def get_keyring_value(profile: str, key: str) -> str | None:
    try:
        return keyring.get_password(service_name(profile), key)
    except Exception as exc:  # noqa: BLE001 - keyring backends raise many concrete errors.
        raise AuthError(f"Could not read {key} from keyring for profile {profile}: {exc}") from exc


def set_keyring_value(profile: str, key: str, value: str) -> None:
    try:
        keyring.set_password(service_name(profile), key, value)
    except Exception as exc:  # noqa: BLE001
        raise AuthError(f"Could not write {key} to keyring for profile {profile}: {exc}") from exc


def delete_keyring_value(profile: str, key: str) -> bool:
    try:
        keyring.delete_password(service_name(profile), key)
        return True
    except keyring.errors.PasswordDeleteError:
        return False
    except Exception as exc:  # noqa: BLE001
        raise AuthError(f"Could not delete {key} from keyring for profile {profile}: {exc}") from exc


def keychain_get(service: str, account: str) -> str | None:
    if platform.system() != "Darwin":
        return None
    completed = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0:
        return (completed.stdout or "").strip() or None
    output = (completed.stderr or completed.stdout or "").lower()
    if "could not be found" in output:
        return None
    raise AuthError(output.strip() or f"security lookup failed for {service}/{account}")


def load_env_credentials(profile: str) -> Credentials | None:
    api_id = os.environ.get(ENV_API_ID)
    api_hash = os.environ.get(ENV_API_HASH)
    session = os.environ.get(ENV_SESSION)
    if not (api_id and api_hash and session):
        return None
    return Credentials(
        profile=profile,
        api_id=int(api_id),
        api_hash=api_hash,
        session=session,
        phone=os.environ.get(ENV_PHONE),
        source="env",
    )


def load_keyring_credentials(profile: str) -> Credentials | None:
    api_id = get_keyring_value(profile, "api_id")
    api_hash = get_keyring_value(profile, "api_hash")
    session = get_keyring_value(profile, "session")
    if not (api_id and api_hash and session):
        return None
    return Credentials(
        profile=profile,
        api_id=int(api_id),
        api_hash=api_hash,
        session=session,
        phone=get_keyring_value(profile, "phone"),
        source="keyring",
    )


def load_telegram_telethon_credentials(profile: str) -> Credentials | None:
    service = f"{TELEGRAM_TELETHON_SERVICE}:{profile}"
    api_id = keychain_get(service, "api_id")
    api_hash = keychain_get(service, "api_hash")
    session = keychain_get(service, "session")
    if not (api_id and api_hash and session):
        return None
    return Credentials(
        profile=profile,
        api_id=int(api_id),
        api_hash=api_hash,
        session=session,
        phone=keychain_get(service, "phone"),
        source="telegram-telethon-keychain",
    )


def load_credentials(profile: str, auth_source: str = "auto") -> Credentials:
    loaders = {
        "env": [load_env_credentials],
        "keyring": [load_keyring_credentials],
        "telegram-telethon": [load_telegram_telethon_credentials],
        "auto": [load_env_credentials, load_keyring_credentials, load_telegram_telethon_credentials],
    }
    if auth_source not in loaders:
        raise AuthError(f"Unknown auth source: {auth_source}")
    errors: list[str] = []
    for loader in loaders[auth_source]:
        try:
            credentials = loader(profile)
        except AuthError as exc:
            errors.append(str(exc))
            continue
        if credentials is not None:
            return credentials
    suffix = f" Errors: {'; '.join(errors)}" if errors else ""
    raise AuthError(f"No Telegram credentials found for profile '{profile}' using source '{auth_source}'.{suffix}")


def store_credentials(profile: str, api_id: int, api_hash: str, phone: str, session: str) -> None:
    set_keyring_value(profile, "api_id", str(api_id))
    set_keyring_value(profile, "api_hash", api_hash)
    set_keyring_value(profile, "phone", phone)
    set_keyring_value(profile, "session", session)


def create_client(credentials: Credentials) -> TelegramClient:
    return TelegramClient(StringSession(credentials.session), credentials.api_id, credentials.api_hash)


async def authorized_client(credentials: Credentials) -> TelegramClient:
    client = create_client(credentials)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise AuthError(f"Stored session for profile '{credentials.profile}' is not authorized.")
    return client


async def login_interactive(profile: str, api_id: int | None, api_hash: str | None, phone: str | None) -> dict[str, Any]:
    if api_id is None:
        api_id = int(input("Telegram api_id: ").strip())
    if api_hash is None:
        api_hash = getpass.getpass("Telegram api_hash: ").strip()
    if phone is None:
        phone = input("Telegram phone (+...): ").strip()

    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            await client.send_code_request(phone)
            code = input("Telegram login code: ").strip()
            try:
                await client.sign_in(phone=phone, code=code)
            except SessionPasswordNeededError:
                password = getpass.getpass("Telegram 2FA password: ")
                await client.sign_in(password=password)
        session = client.session.save()
        store_credentials(profile, api_id, api_hash, phone, session)
        me = await client.get_me()
        return {
            "profile": profile,
            "stored_in": "keyring",
            "user": user_summary(me),
        }
    finally:
        await client.disconnect()


async def status(profile: str, auth_source: str) -> dict[str, Any]:
    credentials = load_credentials(profile, auth_source)
    client = await authorized_client(credentials)
    try:
        me = await client.get_me()
        return {
            "profile": credentials.profile,
            "auth_source": credentials.source,
            "authorized": True,
            "phone": credentials.phone,
            "user": user_summary(me),
        }
    finally:
        await client.disconnect()


def user_summary(user: Any) -> dict[str, Any]:
    return {
        "id": getattr(user, "id", None),
        "username": getattr(user, "username", None),
        "first_name": getattr(user, "first_name", None),
        "last_name": getattr(user, "last_name", None),
        "phone": getattr(user, "phone", None),
    }


def delete_profile(profile: str) -> dict[str, bool]:
    return {key: delete_keyring_value(profile, key) for key in ("api_id", "api_hash", "phone", "session")}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(coro: Any) -> Any:
    return asyncio.run(coro)

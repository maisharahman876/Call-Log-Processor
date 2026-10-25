"""Shared field, timestamp and JSON parsing for the call-log adapters."""

import json
from collections.abc import Mapping
from datetime import datetime, timezone, tzinfo
from email.utils import parsedate_to_datetime
from typing import Any

from ..errors import NormalisationError
from ..models import Direction


def text(value: Any) -> str | None:
    if value is None:
        return None
    
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("expected a string or integer identifier")
    
    return str(value).strip() or None


def required_id(value: Any, field: str) -> str:
    result = text(value)
    
    if result is None:
        raise ValueError(f"missing {field}")
    
    return result


def direction(value: Any) -> Direction:
    key = (text(value) or "").lower()
    
    if key in ("outbound-api", "outbound-dial"):
        return Direction.OUTBOUND
    
    try:
        return Direction(key)
    except ValueError:
        return Direction.UNKNOWN


def timestamp(value: Any, field: str, naive_timezone: tzinfo | None = None) -> datetime | None:
    value = text(value)
    
    if value is None:
        return None

    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            result = parsedate_to_datetime(value)
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError(f"invalid {field} timestamp") from exc
    
    if result.utcoffset() is None:
        if naive_timezone is None:
            raise ValueError(f"{field} timestamp must include a timezone")
        
        # A CSV local time cannot disambiguate a DST fold or a skipped hour.
        candidates = set()

        for fold in (0, 1):
            utc_time = result.replace(tzinfo=naive_timezone, fold=fold).astimezone(timezone.utc)
            local_time = utc_time.astimezone(naive_timezone).replace(tzinfo=None)

            if local_time == result:
                candidates.add(utc_time)
        
        if not candidates:
            raise ValueError(
                f"{field} is a nonexistent local time in {naive_timezone}; "
                "check the source timestamp or supply an explicit UTC offset"
            )
        
        if len(candidates) > 1:
            raise ValueError(f"{field} is an ambiguous local time in {naive_timezone}; supply an explicit UTC offset")
        
        return candidates.pop()
    
    return result.astimezone(timezone.utc)


def json_payload(payload: Any, provider: str) -> Any:
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise NormalisationError(f"{provider}: input must be UTF-8") from exc
    
    if isinstance(payload, str):
        try:
            return json.loads(payload.lstrip("\ufeff"))
        except json.JSONDecodeError as exc:
            raise NormalisationError(f"{provider}: invalid JSON at line {exc.lineno}") from exc
    
    return payload


def records(payload: Any, key: str, provider: str) -> list[Mapping]:
    payload = json_payload(payload, provider)
    
    if isinstance(payload, Mapping):
        if key not in payload:
            return [payload]

        payload = payload[key]
    
    if not isinstance(payload, list) or any(not isinstance(item, Mapping) for item in payload):
        raise NormalisationError(f"{provider}: expected an object or a list of record objects")

    return payload

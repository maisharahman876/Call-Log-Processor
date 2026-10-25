"""Recognise the supported call-log formats from their field signatures."""

import csv
import io
import json
import re
from typing import Any
from urllib.parse import parse_qsl

from .errors import NormalisationError


JSON_SIGNATURES = {
    "twilio": re.compile(r"calls|sid|CallSid"),
    "ringcentral": re.compile(r"records|startTime"),
}

ASTERISK_START = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$")


def _json_providers(payload: Any) -> set[str]:
    if isinstance(payload, list):
        candidates: set[str] = set()

        for item in payload:
            candidates.update(_json_providers(item))

        return candidates

    if not isinstance(payload, dict):
        return set()

    # Match actual keys, ignoring provider-like text in values and metadata.
    candidates = {
        provider for provider, pattern in JSON_SIGNATURES.items()
        if any(isinstance(key, str) and pattern.fullmatch(key) for key in payload)
    }

    for collection in ("calls", "records"):
        if collection in payload:
            candidates.update(_json_providers(payload[collection]))

    return candidates


def _asterisk_csv(payload: str) -> bool:
    reader = csv.reader(io.StringIO(payload, newline=""), strict=True)
    first = next((row for row in reader if any(cell.strip() for cell in row)), None)

    if first is None:
        return False

    if {"src", "dst", "start", "disposition"}.issubset(cell.strip().lower() for cell in first):
        return True

    if len(first) not in (16, 17, 18, 21):
        return False

    return ASTERISK_START.fullmatch(first[9].strip()) is not None


def detect_provider(payload: Any) -> str:
    """Match text, bytes or decoded JSON; reject unknown or mixed formats."""

    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8-sig")

        if isinstance(payload, str):
            payload = payload.lstrip("\ufeff \r\n\t")

            if payload.startswith(("{", "[")):
                payload = json.loads(payload)

            elif _asterisk_csv(payload):
                return "asterisk"

            elif any(key in {"CallSid", "CallStatus"} for key, _ in parse_qsl(payload)):
                return "twilio"

    except (UnicodeError, json.JSONDecodeError, csv.Error) as exc:
        raise NormalisationError(f"cannot detect call-log format: {exc}") from exc

    candidates = _json_providers(payload)

    if len(candidates) == 1:
        return candidates.pop()

    if candidates:
        raise NormalisationError(f"ambiguous call-log format: found {', '.join(sorted(candidates))}; provide one provider at a time")

    raise NormalisationError(
        "cannot detect call-log format: expected Asterisk CSV, Twilio JSON/webhook or RingCentral JSON"
    )

"""RingCentral call-log records and navigation.nextPage pagination."""

from collections.abc import Callable, Iterable, Iterator, Mapping
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from ..errors import NormalisationError
from ..models import Call, Direction, Status
from ._common import direction, json_payload, records, required_id, text, timestamp


STATUSES = {
    "accepted": Status.COMPLETED,
    "call connected": Status.COMPLETED,
    "completed": Status.COMPLETED,
    "in progress": Status.IN_PROGRESS,
    "ringing": Status.RINGING,
    "missed": Status.NO_ANSWER,
    "no answer": Status.NO_ANSWER,
    "busy": Status.BUSY,
    "rejected": Status.REJECTED,
    "failed": Status.FAILED,
    "hang up": Status.CANCELED,
    "cancelled": Status.CANCELED,
    "canceled": Status.CANCELED,
    "voicemail": Status.VOICEMAIL,
}


def _duration(value: Any, field: str) -> timedelta | None:
    if value is None or value == "":
        return None

    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"{field} must be a non-negative number")

    try:
        seconds = Decimal(str(value))

        if field == "durationMs":
            seconds /= 1000

        if not seconds.is_finite() or seconds < 0:
            raise ValueError(f"{field} must be a finite non-negative number")

        return timedelta(microseconds=int(seconds * 1_000_000))

    except (InvalidOperation, OverflowError) as exc:
        raise ValueError(f"invalid {field}") from exc


def _object_field(value: Any, field: str) -> Mapping:
    if value is None:
        return {}

    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")

    return value


class RingCentralAdapter:
    def parse(self, payload: Any) -> list[Call]:
        payload = json_payload(payload, "ringcentral")

        if isinstance(payload, list) and any(isinstance(item, Mapping) and "records" in item for item in payload):
            return self.parse_pages(payload)

        calls = []

        for index, row in enumerate(records(payload, "records", "ringcentral"), 1):
            try:
                if (text(row.get("type")) or "voice").lower() != "voice":
                    continue

                calls.append(self._call(row))
            except (ValueError, OverflowError) as exc:
                raise NormalisationError(f"ringcentral: record {index}: {exc}") from exc

        return calls

    def parse_pages(self, pages: Iterable[Any]) -> list[Call]:
        calls = []

        for index, page in enumerate(pages, 1):
            page = json_payload(page, "ringcentral")

            if not isinstance(page, Mapping) or "records" not in page:
                raise NormalisationError(f"ringcentral: page {index}: expected an object containing records")

            try:
                calls.extend(self.parse(page))
            except NormalisationError as exc:
                raise NormalisationError(f"ringcentral: page {index}: {exc}") from exc

        return calls

    def iter_calls(self, first_page: Any, fetch_page: Callable[[str], Any]) -> Iterator[Call]:
        """Follow nextPage links using a caller-supplied authenticated fetcher."""
        page = json_payload(first_page, "ringcentral")
        visited: set[str] = set()

        while True:
            if not isinstance(page, Mapping) or "records" not in page:
                raise NormalisationError("ringcentral: expected a page containing records")

            try:
                uri = text(page.get("uri"))

                if uri:
                    visited.add(uri)

                navigation = _object_field(page.get("navigation"), "navigation")
                next_page = _object_field(navigation.get("nextPage"), "navigation.nextPage")
                next_uri = text(next_page.get("uri"))
                paging = _object_field(page.get("paging"), "paging")

                if (
                    next_uri is None and "page" in paging and "totalPages" in paging
                    and int(paging["page"]) < int(paging["totalPages"])
                ):
                    raise ValueError("paging indicates more pages but navigation.nextPage.uri is missing")

            except (ValueError, TypeError) as exc:
                raise NormalisationError(f"ringcentral: invalid pagination: {exc}") from exc

            if next_uri in visited:
                raise NormalisationError("ringcentral: pagination cycle detected")

            yield from self.parse(page)

            if next_uri is None:
                return

            visited.add(next_uri)
            page = json_payload(fetch_page(next_uri), "ringcentral")

    @staticmethod
    def _call(row: Mapping) -> Call:
        call_id = required_id(row.get("id"), "id")

        start = timestamp(row.get("startTime"), "startTime")

        if start is None:
            raise ValueError("missing startTime")

        status = STATUSES.get((text(row.get("result")) or "").lower(), Status.UNKNOWN)

        length = _duration(row.get("durationMs"), "durationMs")

        if length is None:
            length = _duration(row.get("duration"), "duration")

        end = None

        if length is not None and status not in (Status.RINGING, Status.IN_PROGRESS):
            end = start + length

        call_direction = direction(row.get("direction"))
        caller = _object_field(row.get("from"), "from")
        callee = _object_field(row.get("to"), "to")

        if call_direction == Direction.INBOUND:
            owner = callee
        elif call_direction == Direction.OUTBOUND:
            owner = caller
        else:
            owner = {}

        extension = text(owner.get("extensionNumber"))

        if extension is None:
            extension = text(_object_field(row.get("extension"), "extension").get("extensionNumber"))

        recording = _object_field(row.get("recording"), "recording")

        return Call(
            id=call_id, start=start, end=end, direction=call_direction,
            caller=text(caller.get("phoneNumber")) or text(caller.get("extensionNumber")),
            callee=text(callee.get("phoneNumber")) or text(callee.get("extensionNumber")),
            extension=extension, status=status, recording=text(recording.get("contentUri")),
        )

"""Twilio REST call resources, call lists, and status callback form data."""

from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl

from ..errors import NormalisationError
from ..models import Call, Status
from ._common import direction, records, required_id, text, timestamp


TERMINAL_STATUSES = {
    Status.COMPLETED, Status.BUSY, Status.NO_ANSWER, Status.FAILED, Status.CANCELED,
}


class TwilioAdapter:
    def parse(self, payload: Any) -> list[Call]:
        if isinstance(payload, bytes):
            try:
                payload = payload.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise NormalisationError("twilio: input must be UTF-8") from exc

        if isinstance(payload, str) and not payload.lstrip("\ufeff \r\n\t").startswith(("{", "[")) and "=" in payload:
            try:
                pairs = parse_qsl(payload.strip(), keep_blank_values=True, strict_parsing=True, errors="strict")
                payload = dict(pairs)

                if len(payload) != len(pairs):
                    raise ValueError("duplicate form fields")

            except ValueError as exc:
                raise NormalisationError(f"twilio: invalid webhook form: {exc}") from exc

        calls = []

        for index, row in enumerate(records(payload, "calls", "twilio"), 1):
            try:
                calls.append(self._call(row))
            except ValueError as exc:
                raise NormalisationError(f"twilio: record {index}: {exc}") from exc

        return calls

    @staticmethod
    def _call(row: Mapping) -> Call:
        webhook = "CallSid" in row
        id_field = "CallSid" if webhook else "sid"
        call_id = required_id(row.get(id_field), id_field)

        status_key = (text(row.get("CallStatus" if webhook else "status")) or "").lower()

        try:
            status = Status(status_key)
        except ValueError:
            status = Status.UNKNOWN

        start = timestamp(row.get("StartTime" if webhook else "start_time"), "start")
        end = timestamp(row.get("EndTime" if webhook else "end_time"), "end")

        if webhook:
            event_time = timestamp(row.get("Timestamp"), "Timestamp")

            if status in TERMINAL_STATUSES:
                end = end or event_time
            elif status == Status.INITIATED:
                start = start or event_time


        return Call(
            id=call_id, start=start, end=end,
            direction=direction(row.get("Direction" if webhook else "direction")),
            caller=text(row.get("From" if webhook else "from")),
            callee=text(row.get("To" if webhook else "to")),
            extension=None,
            status=status,
            recording=text(row.get("RecordingUrl" if webhook else "recording_url")),
        )

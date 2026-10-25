"""Asterisk cdr_csv Master.csv and headered CSV exports."""

import csv
import hashlib
import io
import json
from collections.abc import Iterable, Sequence
from datetime import tzinfo
from itertools import chain
from typing import TextIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..errors import NormalisationError
from ..models import Call, Direction, Status
from ._common import direction, text, timestamp


DEFAULT_TIMEZONE = "Europe/London"

BASE_FIELDS = (
    "accountcode", "src", "dst", "dcontext", "clid", "channel", "dstchannel",
    "lastapp", "lastdata", "start", "answer", "end", "duration", "billsec",
    "disposition", "amaflags",
)
DEFAULT_FIELDS = BASE_FIELDS + ("uniqueid", "userfield")
EXTENDED_FIELDS = DEFAULT_FIELDS + ("peeraccount", "linkedid", "sequence")

STATUSES = {
    "ANSWERED": Status.COMPLETED,
    "NO ANSWER": Status.NO_ANSWER,
    "BUSY": Status.BUSY,
    "FAILED": Status.FAILED,
    "CONGESTION": Status.FAILED,
    "CANCELLED": Status.CANCELED,
    "CANCELED": Status.CANCELED,
}


class AsteriskCDRAdapter:
    def __init__(
        self, *, timezone: str | tzinfo = DEFAULT_TIMEZONE, extensions: Iterable[str] = (),
        inbound_contexts: Iterable[str] = (), outbound_contexts: Iterable[str] = (),
        fieldnames: Sequence[str] | None = None, recording_field: str = "recordingfile",
    ) -> None:
        if isinstance(timezone, tzinfo):
            self.timezone = timezone
        else:
            try:
                self.timezone = ZoneInfo(timezone)
            except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
                raise NormalisationError(f"unknown timezone: {timezone!r}") from exc

        self.extensions = frozenset(str(value) for value in extensions)
        self.inbound_contexts = frozenset(inbound_contexts)
        self.outbound_contexts = frozenset(outbound_contexts)
        self.fieldnames = tuple(name.strip().lower() for name in fieldnames) if fieldnames else None
        self.recording_field = recording_field.strip().lower()

        if self.inbound_contexts & self.outbound_contexts:
            raise NormalisationError("asterisk: inbound and outbound contexts must be distinct")

        if self.fieldnames is not None:
            self._validate_fields(self.fieldnames)

    @staticmethod
    def _validate_fields(fields: Sequence[str]) -> None:
        if len(set(fields)) != len(fields) or any(not name for name in fields):
            raise NormalisationError("asterisk: CSV field names must be non-empty and unique")

        missing = {"src", "dst", "start", "disposition"} - set(fields)

        if missing:
            raise NormalisationError(f"asterisk: missing CSV fields: {', '.join(sorted(missing))}")

    def parse(self, payload: str | bytes | TextIO) -> list[Call]:
        if isinstance(payload, bytes):
            try:
                payload = payload.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise NormalisationError("asterisk: input must be UTF-8") from exc

        if isinstance(payload, str):
            payload = io.StringIO(payload.lstrip("\ufeff"), newline="")

        if not hasattr(payload, "read"):
            raise NormalisationError("asterisk: expected CSV text or a text stream")

        reader = csv.reader(payload, strict=True)

        try:
            rows = (row for row in reader if any(cell.strip() for cell in row))
            first = next(rows, None)

            if first is None:
                return []

            first[0] = first[0].lstrip("\ufeff")
            header = tuple(cell.strip().lower() for cell in first)
            fields = self.fieldnames

            if fields is None and {"src", "dst"}.issubset(header):
                fields = header
                self._validate_fields(fields)
            else:
                rows = chain([first], rows)

            calls = []

            for row in rows:
                row_fields = fields

                if row_fields is None:
                    if len(row) not in (16, 17, 18, 21):
                        raise NormalisationError(
                            f"asterisk: row ending at line {reader.line_num}: unsupported column count; "
                            "supply fieldnames for a custom cdr.conf layout"
                        )

                    row_fields = EXTENDED_FIELDS[:len(row)]

                if len(row) != len(row_fields):
                    raise NormalisationError(f"asterisk: row ending at line {reader.line_num}: column count mismatch")

                try:
                    calls.append(self._call(dict(zip(row_fields, row))))
                except ValueError as exc:
                    raise NormalisationError(f"asterisk: row ending at line {reader.line_num}: {exc}") from exc

            return calls

        except csv.Error as exc:
            raise NormalisationError(f"asterisk: invalid CSV at line {reader.line_num}: {exc}") from exc

    def _call(self, row: dict[str, str]) -> Call:
        caller, callee = text(row.get("src")), text(row.get("dst"))

        start = timestamp(row.get("start"), "start", self.timezone)

        if start is None:
            raise ValueError("missing start")

        call_direction = direction(row.get("direction"))
        source_internal, target_internal = caller in self.extensions, callee in self.extensions
        context = row.get("dcontext", "").strip()

        if call_direction == Direction.UNKNOWN:
            if source_internal and target_internal:
                call_direction = Direction.INTERNAL
            elif context in self.inbound_contexts:
                call_direction = Direction.INBOUND
            elif context in self.outbound_contexts:
                call_direction = Direction.OUTBOUND
            elif source_internal:
                call_direction = Direction.OUTBOUND
            elif target_internal:
                call_direction = Direction.INBOUND

        extension = text(row.get("extension"))

        if extension is None:
            if call_direction in (Direction.INBOUND, Direction.INTERNAL):
                extension = callee
            elif call_direction == Direction.OUTBOUND and source_internal:
                extension = caller
            elif target_internal:
                extension = callee

        call_id = text(row.get("uniqueid"))
        sequence = text(row.get("sequence"))

        if call_id is None:
            canonical = json.dumps(row, sort_keys=True, separators=(",", ":"))
            call_id = "asterisk-" + hashlib.sha256(canonical.encode()).hexdigest()[:24]
        elif sequence is not None:
            call_id += ":" + sequence

        return Call(
            id=call_id, start=start, end=timestamp(row.get("end"), "end", self.timezone),
            direction=call_direction, caller=caller, callee=callee, extension=extension,
            status=STATUSES.get((text(row.get("disposition")) or "").upper(), Status.UNKNOWN),
            recording=text(row.get(self.recording_field)),
        )

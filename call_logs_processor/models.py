"""The provider-independent output contract."""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class Direction(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    INTERNAL = "internal"
    UNKNOWN = "unknown"


class Status(str, Enum):
    QUEUED = "queued"
    INITIATED = "initiated"
    RINGING = "ringing"
    IN_PROGRESS = "in-progress"
    COMPLETED = "completed"
    BUSY = "busy"
    NO_ANSWER = "no-answer"
    FAILED = "failed"
    CANCELED = "canceled"
    REJECTED = "rejected"
    VOICEMAIL = "voicemail"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Call:
    id: str
    start: datetime | None
    end: datetime | None
    direction: Direction
    caller: str | None
    callee: str | None
    extension: str | None
    status: Status
    recording: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("id must be a non-empty string")
        
        if not isinstance(self.direction, Direction) or not isinstance(self.status, Status):
            raise ValueError("direction and status must use their shared enums")
        
        for field in ("caller", "callee", "extension", "recording"):
            value = getattr(self, field)
            
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{field} must be a string or None")
        
        for field in ("start", "end"):
            value = getattr(self, field)

            if value is None:
                continue

            if not isinstance(value, datetime) or value.utcoffset() is None:
                raise ValueError(f"{field} must be a timezone-aware datetime or None")

            object.__setattr__(self, field, value.astimezone(timezone.utc))
        
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("end precedes start")

    def to_dict(self) -> dict[str, str | None]:
        def timestamp(value: datetime | None) -> str | None:
            return value.isoformat().replace("+00:00", "Z") if value is not None else None

        return {
            "id": self.id,
            "start": timestamp(self.start),
            "end": timestamp(self.end),
            "direction": self.direction.value,
            "caller": self.caller,
            "callee": self.callee,
            "extension": self.extension,
            "status": self.status.value,
            "recording": self.recording,
        }

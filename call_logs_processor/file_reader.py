"""Read a call-log file and pass its contents to the normaliser."""

from pathlib import Path

from .models import Call
from .normaliser import normalise


def read_and_normalise(file: str | Path) -> list[Call]:
    """Read a call-log file and automatically select its adapter."""
    return normalise(Path(file).read_text(encoding="utf-8-sig"))

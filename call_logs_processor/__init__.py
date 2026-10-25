"""Universal call-log normaliser."""

from .adapters import AsteriskCDRAdapter, RingCentralAdapter, TwilioAdapter
from .detection import detect_provider
from .errors import NormalisationError
from .file_reader import read_and_normalise
from .models import Call, Direction, Status
from .normaliser import normalise


__all__ = [
    "Call", "Direction", "Status", "NormalisationError", "read_and_normalise", "normalise", "detect_provider",
    "AsteriskCDRAdapter", "TwilioAdapter", "RingCentralAdapter",
]

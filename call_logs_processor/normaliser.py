"""Select a provider adapter and normalise call-log data."""

from typing import Any

from .adapters import AsteriskCDRAdapter, RingCentralAdapter, TwilioAdapter
from .detection import detect_provider
from .models import Call


ADAPTERS = {
    "asterisk": AsteriskCDRAdapter,
    "twilio": TwilioAdapter,
    "ringcentral": RingCentralAdapter,
}


def normalise(payload: Any) -> list[Call]:
    """Detect the input format and normalise it with the matching adapter."""
    if hasattr(payload, "read"):
        payload = payload.read()

    provider = detect_provider(payload)
    adapter = ADAPTERS[provider]
    
    return adapter().parse(payload)

import argparse
import json
import sys
from pathlib import Path

from .errors import NormalisationError
from .file_reader import read_and_normalise


def run() -> int:
    parser = argparse.ArgumentParser(description="Detect and normalise provider call logs to a JSON array of Calls.")
    parser.add_argument("file", type=Path, help="Call-log file to normalise.")
    file = parser.parse_args().file

    try:
        calls = [call.to_dict() for call in read_and_normalise(file)]
    except (NormalisationError, OSError, UnicodeError) as exc:
        print(f"{file}: {exc}", file=sys.stderr)
        return 2
    
    print(json.dumps(calls, indent=2, ensure_ascii=False))
    return 0

"""Small shared boundary for machine-readable command adapters."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from .artifacts import error_result


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def run(action: str, operation: Callable[[], dict[str, Any]]) -> int:
    """Emit one JSON object on success or failure and return a shell status."""

    try:
        emit(operation())
        return 0
    except Exception as error:  # Adapters convert domain exceptions into a stable tool result.
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        emit(error_result(action, error))
        return 1

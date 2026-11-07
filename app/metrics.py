"""Tiny in-process metrics registry exposed in Prometheus text format."""
from __future__ import annotations

import threading
from collections import defaultdict

_LOCK = threading.Lock()
_COUNTERS: dict[str, float] = defaultdict(float)
_GAUGES: dict[str, float] = defaultdict(float)
_LABELS: dict[str, dict[tuple[str, ...], float]] = {}


def inc(name: str, value: float = 1.0, **labels: str) -> None:
    with _LOCK:
        if labels:
            series = _LABELS.setdefault(name, {})
            key = tuple(f"{k}={v}" for k, v in sorted(labels.items()))
            series[key] = series.get(key, 0.0) + value
        else:
            _COUNTERS[name] += value


def gauge(name: str, value: float) -> None:
    with _LOCK:
        _GAUGES[name] = value


def snapshot() -> dict:
    with _LOCK:
        return {
            "counters": dict(_COUNTERS),
            "gauges": dict(_GAUGES),
            "series": {name: dict(series) for name, series in _LABELS.items()},
        }


def render() -> str:
    lines: list[str] = []
    with _LOCK:
        for name, value in sorted(_COUNTERS.items()):
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {value:g}")
        for name, value in sorted(_GAUGES.items()):
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {value:g}")
        for name, series in sorted(_LABELS.items()):
            lines.append(f"# TYPE {name} counter")
            for key, value in sorted(series.items()):
                labels = ",".join(key)
                lines.append(f"{name}{{{labels}}} {value:g}")
    return "\n".join(lines) + "\n"


def reset() -> None:
    with _LOCK:
        _COUNTERS.clear()
        _GAUGES.clear()
        _LABELS.clear()

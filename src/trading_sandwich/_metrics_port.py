"""Per-queue metrics-port allocator. With horizontal scaling (4 feature-worker
replicas in Phase 1), multiple workers on the same host claim ports in the same
queue range. First to bind wins; later workers pick the next free port.
"""
from __future__ import annotations

import socket

_RANGES = {
    "features":  range(9101, 9121),
    "signals":   range(9121, 9125),
    "outcomes":  range(9125, 9129),
    "triage":    range(9129, 9133),
    "execution": range(9133, 9137),
}


def _is_port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def allocate_port(queue: str) -> int:
    """Return the lowest free port in the queue's range, or 0 if all taken or
    queue is unknown. 0 tells start_metrics_server to skip serving.
    """
    port_range = _RANGES.get(queue)
    if port_range is None:
        return 0
    for p in port_range:
        if _is_port_free(p):
            return p
    return 0

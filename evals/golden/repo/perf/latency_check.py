"""Average-load check for the redemption endpoint.

Runs a fixed batch of redemption calls and checks the pipeline is not
regressing under a typical afternoon rush: this answers the average-load
question, "does the system meet its targets on a normal day". It is not a
stress, breakpoint, soak or spike check; those questions are out of scope
for this script.
"""

from __future__ import annotations


def check_average_latency(latencies_ms: list[float], target_ms: float = 200.0) -> bool:
    """Return True if the mean latency across the run is under target_ms."""
    mean_latency = sum(latencies_ms) / len(latencies_ms)
    return mean_latency < target_ms


def run_check(latencies_ms: list[float]) -> None:
    if check_average_latency(latencies_ms):
        print("average-load check: pass")
    else:
        raise SystemExit("average-load check: fail, mean latency over target")

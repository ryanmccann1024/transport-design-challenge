"""Shared acceptance checks for the published cases."""

OUTAGE_OUTCOMES = {
    "camera-status": ("expired", "dropped"),
    "turn-4-replay-tag": ("delivered", "expired", "dropped"),
}


def outage_exceptions(scenario):
    return OUTAGE_OUTCOMES if scenario.name == "outage" else {}


def case_problems(scenario, result):
    problems = {"video": [], "updates": [], "queue": []}
    received = result["upload_received_bytes"]
    complete = result["upload_complete_ms"]
    if received != scenario.upload_bytes or complete is None or complete > scenario.limit_ms:
        problems["video"].append(
            f"received {received:,}/{scenario.upload_bytes:,} bytes by "
            f"{scenario.limit_ms / 1_000:g} s. Check video pacing and queue space."
        )
    peak = result["peak_queue_bytes"]
    if peak > scenario.router_queue_bytes:
        problems["queue"].append(f"peak queue {peak:,} B exceeded {scenario.router_queue_bytes:,} B.")

    exceptions = outage_exceptions(scenario)
    for command in scenario.commands:
        record = result["commands"][command.id]
        status = record["status"]
        allowed = exceptions.get(command.id, ("delivered",))
        if status not in allowed:
            expected = " or ".join(allowed)
            problems["updates"].append(
                f"{command.id}: {status}. Expected {expected} "
                f"(deadline: {command.ttl_ms} ms from creation)."
            )
        elif status == "delivered" and record["latency_ms"] >= command.ttl_ms:
            problems["updates"].append(
                f"{command.id}: arrived in {record['latency_ms']} ms. "
                f"Needed delivery before {command.ttl_ms} ms from creation."
            )
    return problems

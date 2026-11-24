"""Summarize crew-update delivery and latency."""


def command_metrics(records):
    latencies = sorted(
        record["latency_ms"] for record in records.values() if record["status"] == "delivered"
    )
    count = len(latencies)
    # Nearest rank: round the rank up, then convert to a zero-based index.
    return {
        "delivered": count,
        "expired": sum(record["status"] == "expired" for record in records.values()),
        "dropped": sum(record["status"] == "dropped" for record in records.values()),
        "pending": sum(record["status"] == "pending" for record in records.values()),
        **{f"p{p}_ms": latencies[(p * count + 99) // 100 - 1] if count else None
           for p in (50, 90, 99)},
    }


def with_metrics(result):
    return {**result, "command_metrics": command_metrics(result["commands"])}

"""Render the recorded simulation events as a readable timeline."""

from .model import COMMAND_BYTES
from .scenarios import PUBLIC


OUTAGE_DESCRIPTIONS = {
    "camera-status": "Camera status",
    "turn-4-replay-tag": "Turn 4 replay tag",
}


def update_name(scenario, update_id):
    if scenario is PUBLIC["outage"] and update_id in OUTAGE_DESCRIPTIONS:
        return f"{OUTAGE_DESCRIPTIONS[update_id]} ({update_id})"
    return update_id


def timeline(scenario, result, *, events=None, focus=None):
    """Number updates by creation order and update packets by queue admission."""
    updates = {
        item.id: (f"U{number:03d} {update_name(scenario, item.id)}", item.at_ms + item.ttl_ms)
        for number, item in enumerate(sorted(scenario.commands, key=lambda item: item.at_ms), 1)
    }
    active_packets = {}
    packet_count = 0
    for event in result["events"] if events is None else events:
        at_ms = event["at_ms"]
        kind = event["event"]
        update_id = event.get("command")
        if kind == "tick":
            link = "up" if event["online"] else "down"
            queue = " → ".join(f"{event[key]:,}" for key in
                               ("queued_start", "queued_before_send", "queued_end"))
            sent = f'{event["video_sent"]:,.6f}'.rstrip("0").rstrip(".")
            yield at_ms, kind, (f'link {link} | queue {queue} B | pending {event["pending"]} | '
                   f'video queued +{event["video_admitted"]:,} B, sent {sent} B, garage +{event["video_received"]:,} B ({event["video_total_received"]:,} B total)')
        elif kind in ("link_down", "link_up"):
            yield at_ms, kind, f'{kind.replace("_", " ")} ({event["checkpoint_bytes"]:,} B of video received)'
            if kind == "link_down":
                for id_, packet in active_packets.items():
                    if focus is None or focus == id_:
                        yield at_ms, kind, f"{updates[id_][0]}: {packet} cleared on disconnect"
                active_packets.clear()
        elif kind == "upload_complete":
            if focus is None:
                yield at_ms, kind, f'video complete ({event["bytes"]:,} B received)'
        else:
            label, deadline_ms = updates[update_id]
            if kind == "command_created":
                detail = f"created (deadline {deadline_ms / 1_000:.3f} s)"
            elif kind == "command_queued":
                packet_count += 1
                packet = f"P{packet_count:03d}"
                active_packets[update_id] = packet
                detail = f'{packet} queued ({COMMAND_BYTES} B, {event["bytes_ahead"]:,} B ahead)'
            elif kind == "command_sent":
                detail = (f'{active_packets[update_id]} sent this tick '
                          f'(arrival scheduled for {event["arrival_ms"] / 1_000:.3f} s)')
            else:
                packet = active_packets.pop(update_id, None)
                detail = f"{packet} " if packet else ""
                detail += kind.removeprefix("command_")
                if kind == "command_delivered":
                    detail += f' to garage ({result["commands"][update_id]["latency_ms"]} ms since creation)'
                elif kind == "command_dropped":
                    detail += " (on_disconnect=drop)"
            if focus is None or update_id == focus:
                yield at_ms, kind, f"{label}: {detail}"

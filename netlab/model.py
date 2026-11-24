"""Inputs and simulation for one station and one shared upstream FIFO."""

from collections import deque
from dataclasses import asdict, dataclass, fields
import json
from pathlib import Path


TICK_MS = 10
CHUNK_BYTES = 1_000
COMMAND_BYTES = 200
WORK_PER_BYTE = 8_000  # bits * milliseconds; keep fractional byte service exactly


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in [{low}, {high}]")


@dataclass(frozen=True)
class Policy:
    bulk_rate_bps: int
    bulk_queue_bytes: int
    control_first: bool
    on_disconnect: str

    def __post_init__(self):
        _integer(self.bulk_rate_bps, "bulk_rate_bps", 1_000, 100_000_000)
        _integer(self.bulk_queue_bytes, "bulk_queue_bytes", CHUNK_BYTES, 1_048_576)
        if type(self.control_first) is not bool:
            raise ValueError("control_first must be true or false")
        if self.on_disconnect not in ("hold", "drop"):
            raise ValueError("on_disconnect must be hold or drop")


@dataclass(frozen=True)
class Command:
    id: str
    at_ms: int
    ttl_ms: int

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("command ID must be nonempty")
        _integer(self.at_ms, "at_ms", 0, 300_000)
        _integer(self.ttl_ms, "ttl_ms", 1, 300_000)
        if self.at_ms % TICK_MS:
            raise ValueError("command arrival must align to the 10 ms tick")


@dataclass(frozen=True)
class Scenario:
    name: str
    link_bps: int
    one_way_ms: int
    router_queue_bytes: int
    upload_bytes: int
    limit_ms: int
    commands: tuple[Command, ...] = ()
    outages: tuple[tuple[int, int], ...] = ()

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("scenario name must be nonempty")
        _integer(self.link_bps, "link_bps", 1_000, 100_000_000)
        _integer(self.one_way_ms, "one_way_ms", 0, 60_000)
        _integer(self.router_queue_bytes, "router_queue_bytes", CHUNK_BYTES, 1_048_576)
        _integer(self.upload_bytes, "upload_bytes", 1, 100_000_000)
        _integer(self.limit_ms, "limit_ms", TICK_MS, 300_000)
        if self.one_way_ms % TICK_MS or self.limit_ms % TICK_MS:
            raise ValueError("link delay and simulation limit must align to the tick")
        seen = set()
        for command in self.commands:
            if command.id in seen or command.at_ms >= self.limit_ms:
                raise ValueError("command IDs must be unique and arrivals before the limit")
            seen.add(command.id)
        previous_end = 0
        for start, end in self.outages:
            _integer(start, "outage start", 0, self.limit_ms)
            _integer(end, "outage end", 0, self.limit_ms)
            if start < previous_end or start >= end or start % TICK_MS or end % TICK_MS:
                raise ValueError("outages must be ordered, disjoint, nonempty and tick-aligned")
            previous_end = end


def _object(path, cls):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{cls.__name__} must be a JSON object")
    extra = data.keys() - {field.name for field in fields(cls)}
    if extra:
        raise ValueError(f"unknown {cls.__name__.lower()} fields: {', '.join(sorted(extra))}")
    return data


def load_policy(path):
    return Policy(**_object(path, Policy))


def load_scenario(path):
    data = _object(path, Scenario)
    data["commands"] = tuple(Command(**command) for command in data.get("commands", ()))
    data["outages"] = tuple(tuple(interval) for interval in data.get("outages", ()))
    return Scenario(**data)


@dataclass
class _Frame:
    size: int
    offset: int | None = None
    command_id: str | None = None

    def __post_init__(self):
        self.work_left = self.size * WORK_PER_BYTE


class _Simulation:
    def __init__(self, scenario, policy, log):
        self.scenario = scenario
        self.policy = policy
        self.commands = {command.id: command for command in scenario.commands}
        self.future = deque(sorted(scenario.commands, key=lambda command: command.at_ms))
        self.down = deque(scenario.outages)
        self.pending = deque()
        self.fifo = deque()
        self.in_flight = deque()
        self.records = {id_: {"status": "pending", "attempts": 0} for id_ in self.commands}
        self.now = 0
        self.online = True
        self.upload_received = 0
        self.upload_next = 0
        self.upload_complete_ms = None
        self.upload_admitted_bytes = 0
        self.bulk_queued = 0
        self.queued = 0
        self.peak_queued = 0
        self.tokens = 0
        self.events = []
        self.log = log
        self.video_sent_work = 0

    def _event(self, event, **details):
        record = {"at_ms": self.now, "event": event, **details}
        self.events.append(record)
        if self.log is not None:
            self.log(record)

    def _deadline(self, id_):
        command = self.commands[id_]
        return command.at_ms + command.ttl_ms

    def _finish(self, id_, status):
        record = self.records[id_]
        assert record["status"] == "pending", f"command {id_} finished twice"
        record.update(
            status=status,
            resolved_ms=self.now,
            latency_ms=self.now - self.commands[id_].at_ms if status == "delivered" else None,
        )
        self._event("command_" + status, command=id_)

    def _link_state(self):
        while self.down and self.down[0][1] <= self.now:
            self.down.popleft()
        online = not self.down or self.now < self.down[0][0]
        if online == self.online:
            return
        self.online = online
        self._event("link_up" if online else "link_down", checkpoint_bytes=self.upload_received)
        if not online:
            # In-flight frames precede the FIFO, which precedes pending controls.
            lost = [frame for _, frame in self.in_flight] + list(self.fifo)
            controls = [frame.command_id for frame in lost if frame.command_id is not None]
            self.pending.extendleft(reversed(controls))
            self.fifo.clear()
            self.in_flight.clear()
            self.queued = self.bulk_queued = self.tokens = 0
            self.upload_next = self.upload_received

    def _receive(self):
        while self.in_flight and self.in_flight[0][0] <= self.now:
            _, frame = self.in_flight.popleft()
            if frame.command_id is not None:
                id_ = frame.command_id
                self._finish(id_, "delivered" if self.now < self._deadline(id_) else "expired")
            else:
                assert frame.offset == self.upload_received, "upload gap or duplicate"
                self.upload_received += frame.size
                if self.upload_received == self.scenario.upload_bytes:
                    self.upload_complete_ms = self.now
                    self._event("upload_complete", bytes=self.upload_received)

    def _pending_controls(self):
        while self.future and self.future[0].at_ms <= self.now:
            command = self.future.popleft()
            self._event("command_created", command=command.id)
            self.pending.append(command.id)
        kept = deque()
        for id_ in self.pending:
            if self.now >= self._deadline(id_):
                self._finish(id_, "expired")
            elif not self.online and self.policy.on_disconnect == "drop":
                self._finish(id_, "dropped")
            else:
                kept.append(id_)
        self.pending = kept

    def _enqueue(self, frame):
        self.fifo.append(frame)
        self.queued += frame.size
        self.peak_queued = max(self.peak_queued, self.queued)

    def _admit_controls(self):
        while self.pending and self.queued + COMMAND_BYTES <= self.scenario.router_queue_bytes:
            id_ = self.pending.popleft()
            self._enqueue(_Frame(COMMAND_BYTES, command_id=id_))
            self.records[id_]["attempts"] += 1
            self._event("command_queued", command=id_, bytes_ahead=self.queued - COMMAND_BYTES)

    def _admit_bulk(self):
        added = self.policy.bulk_rate_bps * TICK_MS
        self.tokens = min(self.tokens + added, added + CHUNK_BYTES * WORK_PER_BYTE)
        while self.upload_next < self.scenario.upload_bytes:
            size = min(CHUNK_BYTES, self.scenario.upload_bytes - self.upload_next)
            work = size * WORK_PER_BYTE
            if (
                self.tokens < work
                or self.bulk_queued + size > self.policy.bulk_queue_bytes
                or self.queued + size > self.scenario.router_queue_bytes
            ):
                return
            self._enqueue(_Frame(size, offset=self.upload_next))
            self.bulk_queued += size
            self.upload_next += size
            self.upload_admitted_bytes += size
            self.tokens -= work

    def _serve(self):
        budget = self.scenario.link_bps * TICK_MS
        while self.fifo and budget:
            frame = self.fifo[0]
            served = min(budget, frame.work_left)
            frame.work_left -= served
            budget -= served
            if self.log is not None and frame.command_id is None:
                self.video_sent_work += served
            if frame.work_left == 0:
                self.fifo.popleft()
                self.queued -= frame.size
                if frame.command_id is None:
                    self.bulk_queued -= frame.size
                arrival = self.now + TICK_MS + self.scenario.one_way_ms
                self.in_flight.append((arrival, frame))
                if self.log is not None and frame.command_id is not None:
                    self.log({"at_ms": self.now, "event": "command_sent",
                              "command": frame.command_id, "arrival_ms": arrival})

    def run(self):
        for self.now in range(0, self.scenario.limit_ms + 1, TICK_MS):
            if self.log is not None:
                queued_start = self.queued
                admitted_start = self.upload_admitted_bytes
                received_start = self.upload_received
                self.video_sent_work = 0
            # An outage at a tick boundary wins over arrivals at that boundary.
            self._link_state()
            self._receive()
            self._pending_controls()
            done = self.upload_complete_ms is not None and all(
                record["status"] != "pending" for record in self.records.values()
            )
            if not done and self.now < self.scenario.limit_ms and self.online:
                if self.policy.control_first:
                    self._admit_controls()
                    self._admit_bulk()
                else:
                    self._admit_bulk()
                    self._admit_controls()
                queued_before_send = self.queued
                self._serve()
            else:
                queued_before_send = self.queued
            if self.log is not None:
                self.log({
                    "at_ms": self.now, "event": "tick", "online": self.online,
                    "queued_start": queued_start, "queued_before_send": queued_before_send,
                    "queued_end": self.queued, "pending": len(self.pending),
                    "video_admitted": self.upload_admitted_bytes - admitted_start,
                    "video_sent": self.video_sent_work / WORK_PER_BYTE,
                    "video_received": self.upload_received - received_start,
                    "video_total_received": self.upload_received,
                })
            if done:
                break
        return {
            "scenario": self.scenario.name,
            "policy": asdict(self.policy),
            "limit_ms": self.scenario.limit_ms,
            "upload_bytes": self.scenario.upload_bytes,
            "upload_received_bytes": self.upload_received,
            "upload_complete_ms": self.upload_complete_ms,
            "upload_admitted_bytes": self.upload_admitted_bytes,
            "peak_queue_bytes": self.peak_queued,
            "commands": self.records,
            "events": self.events,
        }


def simulate(scenario: Scenario, policy: Policy, *, log=None):
    """Run an independent simulation and return serializable evidence."""
    return _Simulation(scenario, policy, log).run()

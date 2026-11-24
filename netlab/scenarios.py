"""Public scenarios for the creek camera challenge."""

from .model import Command, Scenario

MB = 1_000_000
LINK_BPS = 2_000_000
ONE_WAY_MS = 50
FIFO_BYTES = 262_144


def camera_case(name, size_mb, limit_ms, commands, outages=()):
    return Scenario(
        name, LINK_BPS, ONE_WAY_MS, FIFO_BYTES, size_mb * MB, limit_ms, commands, outages
    )


# The long case supplies enough observations for a descriptive tail percentile.
LARGE_COMMANDS = (
    (Command("log-gauge", 1_500, 850),)
    + tuple(Command(f"operator-{index:03}", 2_000 + 6_000 * (index // 4) + 10 * (index % 4), 850)
            for index in range(118))
    + (Command("check-camera-late", 180_000, 850),)
)

PUBLIC = {
    "healthy": camera_case("healthy", 20, 120_000, (Command("status", 50, 1_200),)),
    "busy": camera_case(
        "busy", 20, 120_000,
        (
            Command("log-gauge", 1_500, 850),
            Command("log-debris", 2_200, 850),
            Command("check-camera", 10_000, 850),
        ),
    ),
    "outage": camera_case(
        "outage", 20, 125_000,
        (
            Command("before", 700, 1_200),
            Command("camera-status", 2_500, 500),
            Command("turn-4-replay-tag", 3_650, 1_500),
            Command("after", 4_500, 1_200),
        ),
        ((2_000, 4_000),),
    ),
    "medium": camera_case(
        "medium", 40, 200_000,
        (Command("log-gauge", 1_500, 850), Command("check-camera-late", 75_000, 850)),
    ),
    "large": camera_case("large", 60, 290_000, LARGE_COMMANDS),
}

EVIDENCE_CASES = tuple(PUBLIC)

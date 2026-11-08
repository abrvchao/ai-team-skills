"""Background phase advancer for TeamSupervisor runs."""
from __future__ import annotations

import argparse
import os
import signal
import time

from supervisor import BridgeClient
from team_supervisor import TeamSupervisor


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI Team automatic supervisor daemon")
    parser.add_argument(
        "--bridge-url",
        default=os.getenv("AGENT_BRIDGE_URL", "http://127.0.0.1:8765"),
    )
    parser.add_argument(
        "--state-dir",
        default=os.getenv("AI_TEAM_RUN_STATE_DIR", ".orchestrator/team-runs"),
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=float(os.getenv("AI_TEAM_SUPERVISOR_POLL_SECONDS", "2")),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    supervisor = TeamSupervisor(
        BridgeClient(args.bridge_url, timeout=10),
        state_dir=args.state_dir,
    )
    stopping = False

    def stop(*_args) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        supervisor.advance_all()
        deadline = time.monotonic() + max(0.1, args.poll_seconds)
        while not stopping and time.monotonic() < deadline:
            time.sleep(min(0.2, deadline - time.monotonic()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

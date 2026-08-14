#!/usr/bin/env python3
"""Standalone traffic-light indicator test.

Use mock mode anywhere. Real GPIO mode is intended for a non-production test target
or a maintenance window on the Raspberry Pi after electrical validation.
"""

from __future__ import annotations

import argparse

from src.indicator.adapter import MockIndicatorAdapter, RaspberryPiGpioAdapter
from src.indicator.controller import IndicatorController
from src.indicator.model import EdgeHealthState


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test the Senior Pomidor edge health indicator")
    parser.add_argument("--real-gpio", action="store_true", help="drive Raspberry Pi BCM GPIO instead of mock output")
    parser.add_argument("--red-pin", type=int, default=17, help="BCM pin for red LED")
    parser.add_argument("--yellow-pin", type=int, default=27, help="BCM pin for yellow LED")
    parser.add_argument("--green-pin", type=int, default=22, help="BCM pin for green LED")
    parser.add_argument("--dwell", type=float, default=0.5, help="seconds per LED during self-test")
    parser.add_argument(
        "--state",
        choices=[state.value for state in EdgeHealthState],
        default=EdgeHealthState.OK.value,
        help="state to leave displayed after self-test",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.real_gpio:
        adapter = RaspberryPiGpioAdapter(
            red_pin=args.red_pin,
            yellow_pin=args.yellow_pin,
            green_pin=args.green_pin,
        )
    else:
        adapter = MockIndicatorAdapter()

    controller = IndicatorController(adapter)
    try:
        controller.set_state(EdgeHealthState(args.state))
        controller.self_test(dwell_seconds=args.dwell)
        if isinstance(adapter, MockIndicatorAdapter):
            print(f"mock indicator final frame: {adapter.last_pattern}")
        else:
            print(f"indicator self-test passed; final state: {controller.state.value}")
    except Exception:
        controller.close()
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

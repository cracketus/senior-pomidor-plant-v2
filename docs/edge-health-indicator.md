# Edge Health Indicator

Issue: #74

This document describes the first reference physical health indicator for the Raspberry Pi edge node.

## Hardware

The prototype reuses a toy three-LED traffic-light PCB. Measured topology:

```text
original supply: 2 x LR41 ~= 3 V

U1 output -> R1 51 ohm -> red LED    -> common GND
U1 output -> R2 33 ohm -> yellow LED -> common GND
U1 output -> R3 51 ohm -> green LED  -> common GND

SW1 -> U1 control input
SW1 -> GND when pressed
```

The original U1 controller is not used by Senior Pomidor. Electrically isolate the three U1-to-resistor paths before connecting external GPIO.

## Raspberry Pi wiring

BCM GPIO defaults used by the standalone test tool:

```text
GPIO17 -> 470 ohm -> R1 51 ohm -> RED    -> GND
GPIO27 -> 470 ohm -> R2 33 ohm -> YELLOW -> GND
GPIO22 -> 470 ohm -> R3 51 ohm -> GREEN  -> GND
```

The extra 470-ohm resistors are mandatory for this reference modification. Do not drive the existing 33/51-ohm LED paths directly from Raspberry Pi GPIO.

The PCB VCC input remains disconnected after U1 is isolated from the LED channels.

## JST prototype convention

Use one 2-pin JST per function:

```text
R:   SIGNAL + GND
Y:   SIGNAL + GND
G:   SIGNAL + GND
BTN: SIGNAL + GND (optional)
```

Keep pin ordering consistent on every connector. Place the added 470-ohm resistors on the Raspberry Pi/adapter side before the cable.

## Status semantics

| State | Reference pattern |
|---|---|
| `STARTUP` | green on/off pulse, 0.5 Hz |
| `OK` | green steady |
| `BACKLOG` | yellow blink, 1 Hz |
| `DEGRADED` | yellow steady |
| `MAINTENANCE` | red + yellow steady |
| `CRITICAL` | red blink, 2 Hz |

Animation is rendered by one daemon worker using a monotonic clock. State changes wake it immediately and never block sensor acquisition. Any GPIO import, initialization, write, or cleanup failure disables only the indicator until the collector is restarted; the collector, spool, and delivery workers continue running.

## Runtime configuration

The production default is `INDICATOR_ENABLED=false`. `INDICATOR_BACKEND=auto` selects `mock` when `MOCK_SENSORS=true` and `gpio` otherwise. Pins default to BCM `17/27/22`; startup, backlog, and critical frequencies default to `0.5/1/2 Hz`. Pins must be unique BCM numbers in the supported range and frequencies must be positive finite values.

Do not set `INDICATOR_ENABLED=true` with the GPIO backend until the exact board traces, common ground, added series resistors, and pin assignments have been checked during a maintenance window.

## Software boundary

```text
aggregate edge health
        |
        v
indicator state mapping
        |
        +--> mock adapter (CI/development)
        |
        `--> Raspberry Pi GPIO adapter
                  |
                  v
            traffic-light PCB
```

The canonical `senior-pomidor.edge.health.v1` aggregator applies the priority `CRITICAL > MAINTENANCE > DEGRADED > BACKLOG > OK`; `STARTUP` is used until the first complete evaluation. The indicator is output-only and is deliberately excluded from aggregation to prevent a feedback cycle. Telemetry reports both `system_health.aggregate` and the indicator snapshot.

## Standalone validation

Before production Raspberry Pi integration:

1. Remove all power from the traffic-light PCB.
2. Verify continuity: U1 is disconnected from R1/R2/R3.
3. Verify each `R -> LED -> GND` path is intact.
4. Test each LED independently from an approximately 3 V standalone source through an added 470-ohm resistor.
5. Verify no future GPIO line is shorted to GND.
6. Run software in mock mode:

```bash
python scripts/health_indicator_smoke.py
```

Expected result includes the final mock frame.

7. On a validated non-production Raspberry Pi or during a maintenance window, run:

```bash
python scripts/health_indicator_smoke.py --real-gpio
```

Custom BCM pins can be supplied with `--red-pin`, `--yellow-pin`, and `--green-pin`.

The real-GPIO test runs red -> yellow -> green and restores the requested final state.

## Optional button

SW1 may later be isolated from U1 and connected as:

```text
GPIO input -> SW1 -> GND
```

with an internal pull-up. The intended first use is a local self-test request. Button support is intentionally not part of the first GPIO implementation.

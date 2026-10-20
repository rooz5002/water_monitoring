# AFS power measurement — results

Generated 2026-10-20 13:49. Board: LilyGo T-SIM7000G (ESP32-D0WD-V3 + SIM7000G), firmware `afs_power_test`. Meter: inline USB power meter at the board's USB-C input (Option B).

Charge (mAh) is measured at the 5 V USB input; with the board's linear charger/LDO the same current is drawn from the cell, so battery-side energy = mAh × V_nom (3.7 V).

## Core results

| Quantity | Value |
|---|---|
| I_sleep (deep sleep, at USB input) | 30.0 mA (readings: 30.0, 30.0, 30.0) |
| t_cycle (active duration) | 160.2 s |
| Charge per active cycle | 5.72 mAh (over 3 cycles) |
| E_active at USB 5 V input | 29.2 mWh |
| E_active at battery (mAh × 3.7 V) | 21.1 mWh |
| I_sleep on the 18650 (multimeter) | 20.00 mA (readings: 20.00, 20.00, 20.00) |
| P_sleep at battery | 80.0 mW (measured on the cell, 20.0 mA @ 4.00 V) |
| E_day at 30-min interval (48 cycles) | 2764.2 mWh/day |
| Battery-only autonomy (2400 mAh, DoD 0.8) | 2.6 days |
| Solar harvest (10 W × 4 h × 0.7) | 28.0 Wh/day vs need 2.76 Wh/day |

Cross-check: phase durations × breakdown currents predict 5.20 mAh per cycle vs 5.72 mAh from the meter totals.

## Phase durations (mean over cycles)

| Phase | Duration (s) |
|---|---|
| wake_check | 0.00 |
| pump_fill | 15.01 |
| sense | 15.02 |
| inference | 0.01 |
| drain | 15.00 |
| dry_blower | 30.00 |
| modem_on | 4.34 |
| gnss | 60.35 |
| transmit | 9.25 |
| modem_off | 0.52 |
| reposition | 10.00 |

Transmissions acknowledged: 3/3; GNSS fixes: 0/3. Total 18.0 mAh / 90.0 mWh over 540 s.

## Per-subsystem breakdown (at USB input)

| Subsystem state | Current (mA) | Power (mW) | Δ vs idle (mA) | Note |
|---|---|---|---|---|
| idle_all | 67 | 343 | — | ok |
| cpu | 91 | 466 | 24 | ok |
| led_red | 73 | 375 | 6 | ok |
| led_green | 73 | 374 | 6 | ok |
| pump | 104 | 531 | 37 | ok |
| blower | 162 | 828 | 95 | ok |
| motor1 | 119 | 608 | 52 | ok |
| motor2 | 119 | 608 | 52 | = motor1 (same model) |
| modem_on | 78 | 399 | 11 | ok |
| gnss | 105 | 538 | 38 | ok |
| modem_reg | 79 | 404 | 12 | registered |
| modem_tx | 98 | 502 | 31 | registered, peak 121 mA |

Only one repositioning motor runs per cycle.

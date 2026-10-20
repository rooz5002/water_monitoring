# Power_measurement — measured energy budget of the AFS electronics

Bench measurement of the station's power consumption on the target board (LilyGO T-SIM7000G: ESP32 + SIM7000G). The test hardware is the full set of electronics: sensor boards, LDR pair, red/green status LEDs, sampling pump, air blower and the repositioning motors.

## Results

| Quantity | Value |
|---|---|
| Active cycle duration | 160 s |
| Charge per active cycle | 5.72 mAh (mean of 3 metered cycles) |
| Energy per active cycle, at the battery | 21.1 mWh (average 0.47 W; peak 0.83 W while the blower runs) |
| Deep-sleep current, battery input at 4.0 V | 20 mA (80 mW) |
| Daily energy, 30-min interval (48 cycles/day) | 2.76 Wh/day (63 % of it in sleep) |
| Battery-only autonomy, 80 % DoD | 2.6 days at 2400 mAh; 4.3 days at 4000 mAh |
| Solar harvest, 10 W × 4 h × 0.7 | ≈ 28 Wh/day (≈ 10× the daily requirement) |
| Data transmissions acknowledged | 3 / 3 |

`E_day = 48 × E_active + P_sleep × (24 h − 48 × t_cycle)`

Per-subsystem currents measured at the 5 V input:

| State | mA | mW |
|---|---|---|
| Idle, all sensors powered | 67 | 343 |
| CPU busy at 240 MHz | 91 | 466 |
| Status LED (red or green) | 73 | 375 |
| Pump | 104 | 531 |
| Air blower | 162 | 828 |
| Repositioning motor | 119 | 608 |
| Modem on, radio off | 78 | 399 |
| GNSS acquisition | 105 | 538 |
| Modem registered (GSM) | 79 | 404 |
| Data transmission (HTTP POST) | 98 (peak 121) | 502 |

Every row is the whole board in that state, so the idle current is included in each one.

The phase model (phase durations × subsystem currents) predicts 5.20 mAh per cycle. The meter totals give 5.72 mAh, a 9 % difference.

Full reports:
- [`results/power_report.md`](results/power_report.md) for a 2400 mAh cell
- [`results/power_report_4000mAh.md`](results/power_report_4000mAh.md) for a 4000 mAh cell (e.g. 21700)

## Method

1. `afs_power_test/` is an Arduino sketch for the T-SIM7000G (ESP32 core 3.3.x). It re-creates the 30-min operating cycle:
   - wake check;
   - pump fill, 15 s;
   - settle and sense, 10 s + 5 s;
   - on-device RBF-SVM inference;
   - drain, 15 s;
   - blower dry, 30 s;
   - modem power-up;
   - GNSS, 60 s timeout;
   - HTTP POST over the cellular link;
   - modem power-down;
   - LDR repositioning, 10 s;
   - deep sleep.

   Each phase is printed as `@PHASE_BEGIN/END` markers with millisecond timestamps. A serial console (any key within 2 s of reset) can hold any single subsystem on, for per-subsystem readings.
2. `afs_measure.py` runs the test from a laptop. It drives the console, records the serial log and asks for the meter readings. Subcommands:
   - `breakdown`: per-subsystem currents
   - `sleep`: deep-sleep current on USB
   - `cycles`: N complete cycles, metered as the difference in the meter's mAh/mWh counters between start and end
   - `sleep-battery`: deep-sleep current measured on the battery input
   - `report`: writes the report
3. Active energy was measured with an inline USB power meter at the 5 V USB-C input, with the cell removed.
   - The board feeds the 3.3 V rail and the modem through a linear charger/LDO path, so the same charge comes from the cell.
   - Battery-side energy was therefore computed as mAh × 3.7 V.
   - The meter's mWh counter (finer than its 1 mAh step) was used for the charge per cycle.
4. Sleep current on USB (30 mA) includes the USB-serial bridge and the USB-powered LED. The value used in the budget was measured on the battery input instead: a regulated supply at 4.0 V took the place of the cell, with USB unplugged.

## Limitations

- The meters average over time. The SIM7000G's millisecond GSM transmit bursts (up to about 2 A) are counted in the charge totals but not shown as peaks.
- The sleep reading's resolution is ±5 mA, because the bench supply displays in 0.01 A steps.
- The tests ran indoors, so GNSS never got a fix and always ran to its 60 s timeout. The GNSS figure is therefore a worst case.
- The modem registered on GSM 900 (about −74 dBm). LTE-M/NB-IoT would draw less during transmit.
- The two repositioning motors are the same model and only one runs per cycle, so one motor was measured and its value is used for both.
- The phase durations are representative values set in the test firmware.
- The 20 mA sleep current comes mostly from the sensor and indicator modules, which stay powered in sleep. The ESP32 and the powered-down modem draw only microamps. Switching off the modules' supply during sleep would cut the sleep current to below 1 mA.

The SIM's public and private IP addresses have been replaced with `x.x.x.x` in `results/logs/`.

## Wiring used

| Item | GPIO |
|---|---|
| TDS / EC / pH / turbidity analog outputs | 32 / 33 / 34 / 39 (5 V outputs through a 10 k / 20 k divider) |
| LDR left / right (LDR to 3V3, 10 kΩ to GND) | 14 / 15 |
| Red / green LED module | 18 / 19 |
| Pump / blower (driver inputs) | 21 / 22 |
| Motor 1 / motor 2 (H-bridge IN1 / IN3; IN2 and IN4 to GND) | 23 / 5 |

The board itself uses GPIO 4, 25, 26 and 27 for the modem, 12 for its LED, 35 for battery sense and 36 for solar sense.

## Reproduce

```
arduino-cli compile --fqbn esp32:esp32:esp32 afs_power_test
arduino-cli upload  --fqbn esp32:esp32:esp32 -p /dev/ttyACM0 afs_power_test
python3 afs_measure.py breakdown
python3 afs_measure.py sleep
python3 afs_measure.py cycles --n 3 --apn <your APN>
python3 afs_measure.py sleep-battery
python3 afs_measure.py report --batt-mah 2400
```

Requires Python 3 with `pyserial`.

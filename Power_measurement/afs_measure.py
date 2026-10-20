#!/usr/bin/env python3
"""Guided AFS power measurement with the afs_power_test firmware + a USB power meter.

The board is driven over USB serial; you read the meter and type the numbers in.
Everything is stored in results/measurements.json, raw serial logs in results/logs/.

  python3 afs_measure.py breakdown            per-subsystem current (procedure section 6)
  python3 afs_measure.py sleep                deep-sleep current       (section 4)
  python3 afs_measure.py cycles --n 5         energy of complete cycles (section 5)
  python3 afs_measure.py report               write results/power_report.md (section 7)

Readings are taken at the USB input (5 V). With a linear charger/LDO path the
charge drawn (mAh) is the same at the battery, so battery-side energy is
reported as mAh x V_nom.
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import serial

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
LOGS = RESULTS / "logs"
DB = RESULTS / "measurements.json"

# Paper-level defaults for the cycle (seconds); override on the command line.
CYCLE_DEFAULTS = dict(fill=15, settle=10, measure=5, drain=15, dry=30, gnss=60, reg=90, move=10)

BREAKDOWN = [
    # key, firmware step, what to tell the user
    ("idle_all", "idle", "ESP32 awake, modem OFF, TDS + EC boards and LDRs connected."),
    ("idle_no_tds", "idle", "Unplug the TDS board's VCC wire (leave EC connected)."),
    ("idle_no_sensors", "idle", "Now also unplug the EC board's VCC wire (= ESP32 + board only)."),
    ("idle_all_again", "idle", "Plug TDS and EC VCC back in."),
    ("cpu", "cpu", "ESP32 running the SVM inference flat out."),
    ("led_red", "ledr", "Red status LED ON."),
    ("led_green", "ledg", "Green status LED ON."),
    ("pump", "pump", "Water pump ON."),
    ("blower", "blower", "Air blower ON."),
    ("motor1", "m1", "Repositioning motor 1 ON."),
    ("motor2", "m2", "Repositioning motor 2 ON."),
    ("modem_on", "modem_on", "SIM7000G powered, RF off (CFUN=0)."),
    ("gnss", "gnss", "GNSS receiver on and searching/tracking."),
    ("modem_reg", "modem_reg", "Modem registered on the network, idle (needs SIM)."),
    ("modem_tx", "modem_tx", "Modem sending HTTP posts back-to-back (needs SIM). Note the PEAK too."),
]
ACTUATORS = {"pump", "blower", "motor1", "motor2"}


# ------------------------------------------------------------------ storage ---
def load_db():
    if DB.exists():
        return json.loads(DB.read_text())
    return {"breakdown": {}, "sleep": [], "cycles": [], "system": {}}


def save_db(db):
    RESULTS.mkdir(exist_ok=True)
    DB.write_text(json.dumps(db, indent=2))


# ------------------------------------------------------------------ board ---
class Board:
    def __init__(self, port, logname):
        LOGS.mkdir(parents=True, exist_ok=True)
        self.log = open(LOGS / f"{datetime.now():%Y%m%d_%H%M%S}_{logname}.log", "w")
        self.s = serial.Serial(port, 115200, timeout=0.2)
        self.t0 = time.time()

    def _line(self):
        raw = self.s.readline()
        if not raw:
            return None
        line = raw.decode(errors="replace").rstrip()
        t = time.time()
        self.log.write(f"{t:.3f}\t{line}\n")
        self.log.flush()
        return t, line

    def expect(self, pattern, timeout, show=True):
        """Read lines until one matches pattern; return (host_time, line, match) or None."""
        end = time.time() + timeout
        rx = re.compile(pattern)
        while time.time() < end:
            got = self._line()
            if not got:
                continue
            t, line = got
            if show and line.strip():
                print(f"   | {line}")
            m = rx.search(line)
            if m:
                return t, line, m
        return None

    def send(self, text):
        self.s.write((text + "\n").encode())

    def console(self):
        """Reset the ESP32 and stop it at the afs> prompt."""
        self.s.dtr = False
        self.s.rts = True
        time.sleep(0.1)
        self.s.rts = False
        if not self.expect(r"press any key", 5, show=False):
            sys.exit("board did not boot - is the afs_power_test firmware flashed?")
        self.send("")
        self.expect(r"afs> ", 3, show=False)

    def set(self, **kv):
        for k, v in kv.items():
            self.send(f"set {k} {v}")
            self.expect(r"apn=", 3, show=False)


# ------------------------------------------------------------------ input ---
def ask_float(prompt, default=None):
    while True:
        txt = input(f"   > {prompt}" + (f" [{default}]" if default is not None else "") + ": ").strip()
        if not txt and default is not None:
            return default
        if txt.lower() in ("s", "skip"):
            return None
        try:
            return float(txt)
        except ValueError:
            print("     number please (or 's' to skip)")


def ask_reading(v_default):
    """Current in mA and voltage, as shown on the meter."""
    a = ask_float("meter CURRENT in A (e.g. 0.135), 's' to skip")
    if a is None:
        return None
    v = ask_float("meter VOLTAGE in V", v_default)
    return {"mA": round(a * 1000, 1), "V": v, "mW": round(a * v * 1000, 1)}


# ------------------------------------------------------------------ commands ---
def cmd_breakdown(args):
    db = load_db()
    b = Board(args.port, "breakdown")
    b.console()
    steps = [s for s in BREAKDOWN if args.actuators or s[0] not in ACTUATORS]
    if args.only:
        steps = [s for s in steps if s[0] in args.only.split(",")]
    if args.skip:
        steps = [s for s in steps if s[0] not in args.skip.split(",")]
    v = 5.1
    for key, fw, text in steps:
        print(f"\n== {key}: {text}")
        if key.startswith("idle_") and key != "idle_all":
            input("   press Enter when done ...")
        b.send(f"step {fw}")
        r = b.expect(r"@STEP_READY,([^,\s]+),?(\S*)", 240)
        status = r[2].group(2) if r else "timeout"
        if status == "NOT_registered":
            print("   !! modem NOT registered - reading will show network SEARCH current")
        print("   let it settle ~5 s, then read the meter")
        reading = ask_reading(v)
        if reading:
            v = reading["V"]
            if key == "modem_tx":
                pk = ask_float("PEAK current seen in A (or 's')")
                reading["peak_mA"] = round(pk * 1000, 1) if pk else None
            reading["status"] = status or "ok"
            reading["when"] = datetime.now().isoformat(timespec="seconds")
            db["breakdown"][key] = reading
            save_db(db)
        b.send("")
        b.expect(r"@STEP_END", 30, show=False)
    print(f"\nsaved to {DB}")


def cmd_sleep(args):
    db = load_db()
    b = Board(args.port, "sleep")
    b.console()
    print(f"\nPutting the board into deep sleep for {args.seconds} s (modem powered down first).")
    b.send(f"sleep {args.seconds}")
    b.expect(r"@SLEEP", 20)
    print("Wait ~30 s for it to settle, then take 3 readings ~10 s apart.")
    readings = []
    for i in range(3):
        r = ask_reading(5.1)
        if r:
            readings.append(r)
    db["sleep"].append({"when": datetime.now().isoformat(timespec="seconds"), "readings": readings,
                        "power_source": args.source})
    save_db(db)
    b.expect(r"@BOOT,timer", args.seconds + 30)
    print(f"saved to {DB}")


def cmd_sleep_battery(args):
    """Board on the 18650 (no USB): multimeter in series with the cell, readings typed in."""
    db = load_db()
    print("Enter 3 multimeter readings of the cell current in mA while the board sleeps.")
    readings = []
    for i in range(3):
        ma = ask_float(f"reading {i + 1} in mA (e.g. 12.4)")
        if ma is not None:
            readings.append(ma)
    v = ask_float("cell voltage V", 3.9)
    db["sleep"].append({"when": datetime.now().isoformat(timespec="seconds"), "power_source": "battery",
                        "readings": [{"mA": m, "V": v, "mW": round(m * v, 2)} for m in readings]})
    save_db(db)
    print(f"saved to {DB}")


def cmd_cycles(args):
    db = load_db()
    b = Board(args.port, "cycles")
    b.console()
    timings = {k: getattr(args, k) for k in CYCLE_DEFAULTS}
    b.set(**timings, sleep=args.sleep, ncycles=args.n, apn=args.apn)
    print(f"\n{args.n} complete cycles, {args.sleep} s deep sleep between them.")
    print("The meter counters do not need clearing: type what they show now, and again at the end.")
    mah0 = ask_float("meter mAh counter NOW (e.g. 711)")
    mwh0 = ask_float("meter mWh counter NOW (e.g. 3931)")
    t_start = time.time()
    b.send("auto")
    cycles, phases, cur = [], {}, None
    while True:
        r = b.expect(r"@(CYCLE_BEGIN|PHASE_END|CYCLE_END|AUTO_DONE)", 3600)
        if not r:
            sys.exit("no output for an hour - aborting")
        t, line, m = r
        f = line.split(",")
        if m.group(1) == "CYCLE_BEGIN":
            cur = {"cycle": int(f[1]), "phases": {}}
        elif m.group(1) == "PHASE_END":
            cur["phases"][f[2]] = int(f[4]) / 1000
        elif m.group(1) == "CYCLE_END":
            cur.update(active_s=int(f[2]) / 1000, tx_ok=f[3] == "1", gnss_fix=f[4] == "1")
            cycles.append(cur)
        elif m.group(1) == "AUTO_DONE":
            t_stop = t
            break
    print("\a\n3) Cycles finished - read the meter NOW (board is idling awake from here on).")
    mah1 = ask_float("meter mAh counter NOW")
    mwh1 = ask_float("meter mWh counter NOW")
    mah = mah1 - mah0 if None not in (mah0, mah1) else None
    mwh = mwh1 - mwh0 if None not in (mwh0, mwh1) else None
    v = ask_float("meter voltage V", 5.1)
    elapsed = t_stop - t_start
    run = {
        "when": datetime.now().isoformat(timespec="seconds"),
        "n": args.n, "sleep_s": args.sleep, "timings": timings,
        "elapsed_s": round(elapsed, 1), "total_mAh": mah, "total_mWh": mwh, "V": v,
        "counters": {"mAh": [mah0, mah1], "mWh": [mwh0, mwh1]},
        "cycles": [{"cycle": c["cycle"], "active_s": c["active_s"], "tx_ok": c["tx_ok"],
                    "gnss_fix": c["gnss_fix"], "phases": c["phases"]} for c in cycles],
    }
    db["cycles"].append(run)
    save_db(db)
    print(f"elapsed {elapsed:.0f} s, {len(cycles)} cycles, {mah} mAh, {mwh} mWh, saved to {DB}")
    if mwh is not None and elapsed > 0:
        print(f"average {mwh / v / elapsed * 3600:.0f} mA - should be roughly 70-200 mA; if not, re-check the counters")


# ------------------------------------------------------------------ report ---
def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def fmt(x, nd=1):
    return "—" if x is None else f"{x:.{nd}f}"


def cmd_report(args):
    db = load_db()
    bd, out = db["breakdown"], []
    w = out.append
    v_nom = args.vnom

    def sleep_of(src):
        return [r for s in db["sleep"] if s.get("power_source", "usb") == src for r in s["readings"]]
    usb_rd, bat_rd = sleep_of("usb"), sleep_of("battery")
    sleep_mA = mean([r["mA"] for r in usb_rd])  # to correct the USB-metered cycle run
    sleep_rd = [r["mA"] for r in usb_rd]
    bat_mA = mean([r["mA"] for r in bat_rd])
    bat_V = mean([r["V"] for r in bat_rd])

    run = db["cycles"][-1] if db["cycles"] else None
    t_cycle = q_active = e_active_usb = None
    if run:
        t_cycle = mean([c["active_s"] for c in run["cycles"]]) + 0.3  # + ROM/boot time
        n, sleep_total = run["n"], (run["n"] - 1) * run["sleep_s"]
        if run["total_mAh"] is not None and sleep_mA is not None:
            q_active = (run["total_mAh"] - sleep_mA * sleep_total / 3600) / n
        if run["total_mWh"] is not None and sleep_mA is not None:
            e_active_usb = (run["total_mWh"] - sleep_mA * run["V"] * sleep_total / 3600) / n
            q_active = e_active_usb / run["V"]  # finer than the meter's 1 mAh counter

    # Actuators on a separate supply are invisible to the USB meter: add
    # (measured power x phase duration) for each one.
    e_act = None
    if args.actuators_external and run:
        ph = lambda p: mean([c["phases"].get(p) for c in run["cycles"]]) or 0
        runs = {"pump": ph("pump_fill") + ph("drain"), "blower": ph("dry_blower"), "motor1": ph("reposition")}
        e_act = sum(bd[k]["mW"] * t / 3600 for k, t in runs.items() if k in bd)
    e_active_bat = q_active * v_nom if q_active is not None else None
    if e_act is not None and e_active_bat is not None:
        e_active_bat += e_act
    if bat_mA is not None:
        p_sleep_bat, sleep_src = bat_mA * bat_V, f"measured on the cell, {bat_mA:.1f} mA @ {bat_V:.2f} V"
    else:
        p_sleep_bat = sleep_mA * v_nom if sleep_mA is not None else None
        sleep_src = "USB reading × V_nom (includes USB-serial chip; upper bound)"
    cycles_day = 86400 / args.interval
    e_day = None
    if e_active_bat is not None and p_sleep_bat is not None:
        sleep_h = 24 - cycles_day * t_cycle / 3600
        e_day = cycles_day * e_active_bat + p_sleep_bat * sleep_h

    w("# AFS power measurement — results\n")
    w(f"Generated {datetime.now():%Y-%m-%d %H:%M}. Board: LilyGo T-SIM7000G (ESP32-D0WD-V3 + SIM7000G), "
      "firmware `afs_power_test`. Meter: inline USB power meter at the board's USB-C input (Option B).\n")
    w("Charge (mAh) is measured at the 5 V USB input; with the board's linear charger/LDO the same "
      f"current is drawn from the cell, so battery-side energy = mAh × V_nom ({v_nom} V).\n")

    w("## Core results\n")
    w("| Quantity | Value |\n|---|---|")
    w(f"| I_sleep (deep sleep, at USB input) | {fmt(sleep_mA)} mA (readings: {', '.join(fmt(x) for x in sleep_rd) or '—'}) |")
    w(f"| t_cycle (active duration) | {fmt(t_cycle)} s |")
    if run:
        w(f"| Charge per active cycle | {fmt(q_active, 2)} mAh (over {run['n']} cycles) |")
        w(f"| E_active at USB 5 V input | {fmt(e_active_usb)} mWh |")
    if e_act is not None:
        w(f"| Actuators on separate supply (pump, blower, motor 1), added | {fmt(e_act)} mWh |")
    w(f"| E_active at battery (mAh × {v_nom} V{' + actuators' if e_act is not None else ''}) | {fmt(e_active_bat)} mWh |")
    if bat_rd:
        w(f"| I_sleep on the 18650 (multimeter) | {fmt(bat_mA, 2)} mA (readings: {', '.join(fmt(r['mA'], 2) for r in bat_rd)}) |")
    w(f"| P_sleep at battery | {fmt(p_sleep_bat)} mW ({sleep_src}) |")
    w(f"| E_day at {args.interval/60:.0f}-min interval ({cycles_day:.0f} cycles) | {fmt(e_day)} mWh/day |")
    if e_day:
        e_batt = args.batt_mah * v_nom / 1000 * args.dod
        harvest = args.panel_w * args.sun_h * 0.7
        w(f"| Battery-only autonomy ({args.batt_mah:.0f} mAh, DoD {args.dod}) | {e_batt / (e_day / 1000):.1f} days |")
        w(f"| Solar harvest ({args.panel_w} W × {args.sun_h} h × 0.7) | {harvest:.1f} Wh/day vs need {e_day/1000:.2f} Wh/day |")
    w("")

    model = {"pump_fill": "pump", "sense": "idle_all", "drain": "pump", "dry_blower": "blower",
             "modem_on": "modem_on", "gnss": "gnss", "transmit": "modem_tx", "reposition": "motor1"}
    if run and all(v in bd for v in model.values()):
        ph = lambda p: mean([c["phases"].get(p) for c in run["cycles"]]) or 0
        led = bd["led_green"]["mA"] - bd["idle_all"]["mA"] if "led_green" in bd else 0  # status LED on all cycle
        q_model = (sum(ph(p) * bd[k]["mA"] for p, k in model.items()) + led * sum(map(ph, model))) / 3600
        w(f"Cross-check: phase durations × breakdown currents predict {q_model:.2f} mAh per cycle "
          f"vs {fmt(q_active, 2)} mAh from the meter totals.\n")

    if run:
        w("## Phase durations (mean over cycles)\n")
        names = list(run["cycles"][0]["phases"])
        w("| Phase | Duration (s) |\n|---|---|")
        for p in names:
            w(f"| {p} | {fmt(mean([c['phases'].get(p) for c in run['cycles']]), 2)} |")
        tx = sum(c["tx_ok"] for c in run["cycles"])
        fx = sum(c["gnss_fix"] for c in run["cycles"])
        w(f"\nTransmissions acknowledged: {tx}/{len(run['cycles'])}; GNSS fixes: {fx}/{len(run['cycles'])}. "
          f"Total {run['total_mAh']} mAh / {run['total_mWh']} mWh over {run['elapsed_s']:.0f} s.\n")

    w("## Per-subsystem breakdown (at USB input)\n")
    w("| Subsystem state | Current (mA) | Power (mW) | Δ vs idle (mA) | Note |\n|---|---|---|---|---|")
    base = bd.get("idle_all", {}).get("mA")
    for key, _, text in BREAKDOWN:
        r = bd.get(key)
        if not r and key == "motor2" and bd.get("motor1"):
            r = dict(bd["motor1"], status="= motor1 (same model)")
        if not r:
            continue
        d = r["mA"] - base if base is not None and not key.startswith("idle") else None
        note = r.get("status", "")
        if r.get("peak_mA"):
            note += f", peak {r['peak_mA']:.0f} mA"
        w(f"| {key} | {r['mA']:.0f} | {r['mW']:.0f} | {fmt(d, 0)} | {note} |")
    w("\nOnly one repositioning motor runs per cycle.\n")
    path = RESULTS / "power_report.md"
    RESULTS.mkdir(exist_ok=True)
    path.write_text("\n".join(out))
    print("\n".join(out))
    print(f"\nwritten to {path}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", default="/dev/ttyACM0")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("breakdown")
    s.add_argument("--actuators", action="store_true", help="include pump/blower/motors")
    s.add_argument("--only", help="comma list of step keys")
    s.add_argument("--skip", help="comma list of step keys to leave out")
    s.set_defaults(fn=cmd_breakdown)

    s = sub.add_parser("sleep")
    s.add_argument("--seconds", type=int, default=120)
    s.add_argument("--source", default="usb", help="usb or battery")
    s.set_defaults(fn=cmd_sleep)

    s = sub.add_parser("sleep-battery", help="type in multimeter readings taken on the 18650")
    s.set_defaults(fn=cmd_sleep_battery)

    s = sub.add_parser("cycles")
    s.add_argument("--n", type=int, default=3)
    s.add_argument("--sleep", type=int, default=30, help="deep sleep between cycles (s)")
    s.add_argument("--apn", default="cmnet")
    for k, v in CYCLE_DEFAULTS.items():
        s.add_argument(f"--{k}", type=int, default=v)
    s.set_defaults(fn=cmd_cycles)

    s = sub.add_parser("report")
    s.add_argument("--vnom", type=float, default=3.7, help="battery nominal voltage")
    s.add_argument("--batt-mah", type=float, default=3000)
    s.add_argument("--dod", type=float, default=0.8)
    s.add_argument("--panel-w", type=float, default=10)
    s.add_argument("--sun-h", type=float, default=4)
    s.add_argument("--interval", type=int, default=1800, help="sampling interval (s)")
    s.add_argument("--actuators-external", action="store_true",
                   help="pump/blower/motors powered outside the USB meter: add their energy")
    s.set_defaults(fn=cmd_report)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

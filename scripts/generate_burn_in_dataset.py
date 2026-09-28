"""
Synthetic Burn-In Test Dataset Generator — Hierarchical / Batch-Realistic
(Leakage-current-only version)
"""

import csv
import sys
import random
import datetime
import numpy as np

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

RANDOM_SEED = 42
NUM_RECORDS = 10000
OUTPUT_PATH = "burn_in_dataset.csv"

NUM_BATCHES = 20
STRESS_TYPES = ["Thermal-High", "Radiation-Low", "Cycling-Medium"]

FAB_CODE = "ISRO-SCL"         
LOT_START_DATE = datetime.date(2025, 1, 6) 

# Realistic measurement bounds 
LEAKAGE_MIN, LEAKAGE_MAX = 0.1, 5.0           # µA

# Instrument resolution 
LEAKAGE_RESOLUTION = 0.001     # µA

# --- Label decision thresholds -------------------------------------------
FAIL_LEAKAGE_DRIFT = 3.30       # +330% or more

BORDERLINE_LEAKAGE_DRIFT = 1.45

# --- Traditional (fixed-limit) burn-in pass/fail criteria -----------------
DATASHEET_LEAKAGE_MAX = 3.0            # µA — must stay at/below this

# --- Sudden / step-change failures -----------------------------------------
STEP_CHANGE_RATE = 0.04   # fraction of components that get a sudden jump
STEP_CHANGE_CHECKPOINTS = [2, 3]   # which checkpoint index it can hit (96h, 168h)
STEP_CHANGE_LEAKAGE_JUMP = (3.0, 8.0)     # multiplicative jump

# --- Data-quality corruption (logging artifacts, not physical reality) -----
MISSING_READING_RATE = 0.015   # per eligible cell, blanked out
BAD_SENSOR_RATE = 0.005        # per eligible cell, replaced with a fault code
SENSOR_FAULT_VALUE = -1.0      # implausible sentinel (real value can't be negative)

# Stress-type sensitivity multipliers (leakage only)
STRESS_SENSITIVITY = {
    "Thermal-High":    1.35,
    "Radiation-Low":   0.90,
    "Cycling-Medium":  1.00,
}

# Illustrative numeric stress levels
STRESS_LEVEL_RANGES = {
    "Thermal-High":   {"unit": "°C",    "range": (125.0, 150.0)},   # bake temp
    "Radiation-Low":  {"unit": "krad",  "range": (10.0, 50.0)},     # TID dose
    "Cycling-Medium": {"unit": "cycles", "range": (100, 500)},      # thermal cycles
}

PART_TYPES = [
    {"type": "MOSFET",                "family": "IRF-Series N-Channel"},
    {"type": "Op-Amp IC",             "family": "LM-Series Operational Amplifier"},
    {"type": "Voltage Regulator IC",  "family": "7800-Series Linear Regulator"},
    {"type": "Digital Logic IC",      "family": "74HC-Series Logic Gate"},
]

def clip(value, lo, hi):
    return float(np.clip(value, lo, hi))


def round_to_resolution(value, resolution):
    return round(round(value / resolution) * resolution, 6)


# ---------------------------------------------------------------------------
# Batch-level process profiles 
# ---------------------------------------------------------------------------

def make_batch_lot_code(n):
    lot_date = LOT_START_DATE + datetime.timedelta(days=7 * (n - 1))
    date_code = lot_date.strftime("%y%m%d")
    return f"{FAB_CODE}-{date_code}-{n:02d}"


def build_batch_profiles(rng, np_rng):
    profiles = {}
    for n in range(1, NUM_BATCHES + 1):
        batch_id = make_batch_lot_code(n)
        part = rng.choice(PART_TYPES)

        lot_date = LOT_START_DATE + datetime.timedelta(days=7 * (n - 1))
        test_start_dt = datetime.datetime.combine(lot_date, datetime.time(0, 0)) \
            + datetime.timedelta(days=rng.randint(5, 20), hours=rng.randint(0, 23))

        profiles[batch_id] = {
            "part_type": part["type"],
            "part_family": part["family"],
            "test_start_dt": test_start_dt,
            # Baseline offsets: some lots run leakier/tighter than others
            "leak_offset": rng.uniform(-0.15, 0.15),
            # Quality bias: some lots are just built better/worse 
            "severity_bias": clip(float(np_rng.lognormal(mean=0.0, sigma=0.35)), 0.4, 2.5),
            # Low sigma -> tightly controlled lot (low variance).
            # High sigma -> loosely controlled lot (high variance).
            "spread_sigma": rng.uniform(0.15, 0.55),
        }
    return profiles


def generate_checkpoint_timestamps(rng, batch_profile):
    start = batch_profile["test_start_dt"]
    actual_hours = [
        0.0,
        24 + rng.gauss(0, 0.4),
        96 + rng.gauss(0, 0.6),
        168 + rng.gauss(0, 0.6),
    ]
    timestamps = [start + datetime.timedelta(hours=h) for h in actual_hours]
    return timestamps


def generate_component_trace(rng, np_rng, batch_profile, stress_type):
    leak_mult = STRESS_SENSITIVITY[stress_type]

    base_severity = float(np_rng.beta(1.6, 5.0)) * 0.9
    within_batch_noise = float(np_rng.lognormal(mean=0.0, sigma=batch_profile["spread_sigma"]))
    severity = base_severity * batch_profile["severity_bias"] * within_batch_noise

    leak_severity = severity * leak_mult

    # --- Leakage current (µA) --------------------------------------------
    leak_0 = clip(rng.uniform(0.1, 1.0) + batch_profile["leak_offset"],
                  LEAKAGE_MIN, LEAKAGE_MAX)
    leakage = [leak_0]
    for _ in range(3):
        growth = leakage[-1] * leak_severity * rng.uniform(0.5, 1.5) + abs(rng.gauss(0, 0.03))
        instrument_noise = rng.gauss(0, 0.015)  # measurement read noise
        noisy = leakage[-1] + growth + instrument_noise
        leakage.append(clip(noisy, LEAKAGE_MIN, LEAKAGE_MAX))

    # Round to realistic instrument resolution
    leakage = [round_to_resolution(v, LEAKAGE_RESOLUTION) for v in leakage]

    return leakage


def generate_stress_level(rng, stress_type):
    spec = STRESS_LEVEL_RANGES[stress_type]
    lo, hi = spec["range"]
    if spec["unit"] == "cycles":
        value = rng.randint(int(lo), int(hi))
    else:
        value = round(rng.uniform(lo, hi), 1)
    return value, spec["unit"]


def classify_traditional_result(leakage):
    leak_ok = leakage[-1] <= DATASHEET_LEAKAGE_MAX
    return "Pass" if leak_ok else "Fail"


def inject_step_change(rng, leakage):
    if rng.random() >= STEP_CHANGE_RATE:
        return leakage, False

    idx = rng.choice(STEP_CHANGE_CHECKPOINTS)
    factor = rng.uniform(*STEP_CHANGE_LEAKAGE_JUMP)
    for i in range(idx, 4):
        leakage[i] = clip(leakage[i] * factor, LEAKAGE_MIN, LEAKAGE_MAX)

    return leakage, True


def corrupt_readings(rng, record):
    eligible_fields = [
        "Leakage_24h", "Leakage_96h", "Leakage_168h",
    ]
    num_missing = 0
    num_bad = 0
    for field in eligible_fields:
        roll = rng.random()
        if roll < MISSING_READING_RATE:
            record[field] = None
            num_missing += 1
        elif roll < MISSING_READING_RATE + BAD_SENSOR_RATE:
            record[field] = SENSOR_FAULT_VALUE
            num_bad += 1
    return record, num_missing, num_bad


def classify_label(leakage):
    leak_drift = (leakage[-1] - leakage[0]) / max(leakage[0], 1e-6)

    if leak_drift > FAIL_LEAKAGE_DRIFT:
        return "Fail"

    if leak_drift > BORDERLINE_LEAKAGE_DRIFT:
        return "Borderline"

    return "Safe"


def generate_dataset(num_records=NUM_RECORDS, seed=RANDOM_SEED):
    rng = random.Random(seed)
    np_rng = np.random.default_rng(seed)

    batch_profiles = build_batch_profiles(rng, np_rng)
    batch_ids = list(batch_profiles.keys())

    # Uneven batch sizes (real wafer lots are rarely tested in equal numbers) 
    raw_weights = np_rng.dirichlet(np.ones(len(batch_ids)) * 4.0)
    batch_weights = raw_weights.tolist()

    records = []
    for i in range(num_records):
        component_id = f"C{500 + i + 1}"
        batch_id = rng.choices(batch_ids, weights=batch_weights, k=1)[0]
        stress_type = rng.choice(STRESS_TYPES)

        leakage = generate_component_trace(
            rng, np_rng, batch_profiles[batch_id], stress_type
        )
        leakage, had_step_change = inject_step_change(rng, leakage)
        label = classify_label(leakage)
        traditional_result = classify_traditional_result(leakage)
        stress_level, stress_unit = generate_stress_level(rng, stress_type)
        timestamps = generate_checkpoint_timestamps(rng, batch_profiles[batch_id])

        record = {
            "Component_ID": component_id,
            "Batch_ID": batch_id,
            "Part_Type": batch_profiles[batch_id]["part_type"],
            "Part_Family": batch_profiles[batch_id]["part_family"],
            "Proxy_Stress": stress_type,
            "Stress_Level": stress_level,
            "Stress_Unit": stress_unit,
            "Timestamp_0h": timestamps[0].isoformat(sep=" "),
            "Timestamp_24h": timestamps[1].isoformat(sep=" "),
            "Timestamp_96h": timestamps[2].isoformat(sep=" "),
            "Timestamp_168h": timestamps[3].isoformat(sep=" "),
            "Leakage_0h": leakage[0],
            "Leakage_24h": leakage[1],
            "Leakage_96h": leakage[2],
            "Leakage_168h": leakage[3],
            "Traditional_Test_Result": traditional_result,
            "Label": label,
        }

        record, num_missing, num_bad = corrupt_readings(rng, record)
        record["_had_step_change"] = had_step_change   # stripped before CSV write
        record["_num_missing"] = num_missing
        record["_num_bad"] = num_bad

        records.append(record)

    return records


FIELDNAMES = [
    "Component_ID", "Batch_ID", "Part_Type", "Part_Family",
    "Proxy_Stress", "Stress_Level", "Stress_Unit",
    "Timestamp_0h", "Timestamp_24h", "Timestamp_96h", "Timestamp_168h",
    "Leakage_0h", "Leakage_24h", "Leakage_96h", "Leakage_168h",
    "Traditional_Test_Result", "Label",
]


def write_csv(records, output_path):
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, quoting=csv.QUOTE_MINIMAL,
                                 extrasaction="ignore")
        writer.writeheader()
        for r in records:
            writer.writerow(r)


def main():
    records = generate_dataset(NUM_RECORDS)
    write_csv(records, OUTPUT_PATH)

    from collections import Counter
    label_counts = Counter(r["Label"] for r in records)
    stress_counts = Counter(r["Proxy_Stress"] for r in records)
    batch_counts = Counter(r["Batch_ID"] for r in records)
    total = len(records)

    print(f"Generated {total} records -> {OUTPUT_PATH}")
    print("Label distribution:", {k: f"{v} ({v/total:.1%})" for k, v in label_counts.items()})
    print("Stress distribution:", dict(stress_counts))
    print(f"Batches used: {len(batch_counts)} "
          f"(min {min(batch_counts.values())}, max {max(batch_counts.values())} per batch)")

    # "Hidden defect" check: parts that Pass the old fixed-limit test but
    # are still flagged Borderline/Fail by the AI drift-based Label.
    hidden = [r for r in records
              if r["Traditional_Test_Result"] == "Pass" and r["Label"] in ("Borderline", "Fail")]
    trad_pass = sum(1 for r in records if r["Traditional_Test_Result"] == "Pass")
    print(f"Traditional test Pass: {trad_pass} ({trad_pass/total:.1%})")
    print(f"Hidden defects (Traditional Pass, AI Borderline/Fail): "
          f"{len(hidden)} ({len(hidden)/total:.1%})")

    step_changes = sum(1 for r in records if r["_had_step_change"])
    total_missing = sum(r["_num_missing"] for r in records)
    total_bad = sum(r["_num_bad"] for r in records)
    rows_with_any_issue = sum(1 for r in records if r["_num_missing"] > 0 or r["_num_bad"] > 0)
    print(f"Step-change (sudden failure) components: {step_changes} ({step_changes/total:.1%})")
    print(f"Missing readings: {total_missing} cells | Bad-sensor readings: {total_bad} cells")
    print(f"Rows with at least one data-quality issue: {rows_with_any_issue} ({rows_with_any_issue/total:.1%})")


if __name__ == "__main__":
    main()

"""
Synthetic Healthcare Data Generator
====================================
Generates realistic-looking PHI/PII data for a healthcare analytics platform.

Design decisions :
- Referential integrity guaranteed: every claim FK resolves to a real member/provider.
- Deliberately seeded data quality issues for the Day 2 DQ framework to catch.
- Claims are date-partitioned (one CSV per service date) to simulate how real
  source systems land files — this makes COPY INTO with file pattern matching
  actually meaningful instead of contrived.
- JSON files are newline-delimited (NDJSON), which is Snowflake's preferred
  format for loading semi-structured data via COPY INTO.

Total output: ~50 MB across ~15 files. Generation time: ~90 seconds.
"""

import csv
import json
import random
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from faker import Faker

# Reproducibility — same seed = same data every run. Critical for debugging.
SEED = 42
random.seed(SEED)
fake = Faker("en_US")
Faker.seed(SEED)

# Output directory (gitignored — we don't commit 50 MB of fake PHI to GitHub)
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# Volume knobs — interview-defensible sizes
N_MEMBERS = 50_000
N_PROVIDERS = 2_000
N_CLAIMS = 500_000
N_PHARMACY_FILLS = 100_000
N_CLINICAL_EVENTS = 50_000

# Claims are spread across 7 daily partitions to demo partitioned loading
CLAIM_DAYS = 7
CLAIM_END_DATE = date(2025, 1, 7)

# ---------------------------------------------------------------------
# Reference data — realistic healthcare codes
# ---------------------------------------------------------------------
ICD10_CODES = [
    ("E11.9", "Type 2 diabetes without complications"),
    ("I10",   "Essential hypertension"),
    ("J45.909", "Unspecified asthma"),
    ("M54.5", "Low back pain"),
    ("F41.1", "Generalized anxiety disorder"),
    ("K21.9", "GERD without esophagitis"),
    ("N39.0", "Urinary tract infection"),
    ("R51",   "Headache"),
    ("J06.9", "Acute upper respiratory infection"),
    ("Z00.00","General adult medical exam"),
]

CPT_CODES = [
    ("99213", "Office visit, established patient, low complexity"),
    ("99214", "Office visit, established patient, moderate complexity"),
    ("99203", "Office visit, new patient, low complexity"),
    ("80053", "Comprehensive metabolic panel"),
    ("85025", "Complete blood count with differential"),
    ("93000", "Electrocardiogram, routine"),
    ("71046", "Chest X-ray, 2 views"),
    ("90471", "Immunization administration"),
    ("99396", "Preventive visit, established patient, 40-64 yrs"),
    ("36415", "Routine venipuncture"),
]

SPECIALTIES = [
    "Family Medicine", "Internal Medicine", "Cardiology", "Endocrinology",
    "Pulmonology", "Psychiatry", "Orthopedics", "Pediatrics",
    "Emergency Medicine", "Gastroenterology",
]

PLAN_TYPES = ["HMO", "PPO", "EPO", "POS"]
CLAIM_STATUSES = ["PAID", "PAID", "PAID", "PAID", "DENIED", "PENDING", "SUBMITTED"]
NETWORK_STATUSES = ["IN_NETWORK", "IN_NETWORK", "IN_NETWORK", "OUT_OF_NETWORK"]

DRUGS = [
    ("00378-0208-10", "Metformin 500mg"),
    ("00781-1506-10", "Lisinopril 10mg"),
    ("00093-0058-01", "Atorvastatin 20mg"),
    ("00378-0445-77", "Sertraline 50mg"),
    ("00173-0682-20", "Albuterol Inhaler"),
    ("00093-7392-56", "Omeprazole 20mg"),
    ("00378-0211-10", "Amlodipine 5mg"),
    ("00781-1644-10", "Levothyroxine 50mcg"),
]

PHARMACIES = [
    ("1234567890", "CVS Pharmacy #4521"),
    ("2345678901", "Walgreens #8832"),
    ("3456789012", "Rite Aid #1102"),
    ("4567890123", "Walmart Pharmacy #2210"),
    ("5678901234", "Kroger Pharmacy #5544"),
]

LOINC_OBSERVATIONS = [
    ("8480-6",  "systolic_bp",          (90, 180),  "mmHg"),
    ("8462-4",  "diastolic_bp",         (60, 110),  "mmHg"),
    ("8867-4",  "heart_rate",           (55, 110),  "bpm"),
    ("8310-5",  "body_temperature",     (97.0, 100.5), "F"),
    ("2345-7",  "glucose",              (70, 250),  "mg/dL"),
    ("2093-3",  "total_cholesterol",    (140, 280), "mg/dL"),
]

ENCOUNTER_TYPES = ["office_visit", "office_visit", "telehealth", "emergency", "inpatient"]


def generate_npi() -> str:
    """10-digit National Provider Identifier (real format, fake number)."""
    return str(random.randint(1_000_000_000, 9_999_999_999))


# ---------------------------------------------------------------------
# 1. Members (50,000) — CSV with PHI
# ---------------------------------------------------------------------
def generate_members():
    print(f"Generating {N_MEMBERS:,} members...")
    members = []
    path = OUTPUT_DIR / "members.csv"

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        w.writerow([
            "member_id", "ssn", "first_name", "last_name", "dob",
            "gender", "email", "phone",
            "address_line1", "city", "state", "zip",
            "plan_type", "enrollment_date", "termination_date", "updated_at",
        ])
        for i in range(N_MEMBERS):
            member_id = f"M{i+1:08d}"
            enrollment = fake.date_between(start_date="-5y", end_date="-30d")
            # 10% have terminated coverage
            termination = (
                fake.date_between(start_date=enrollment, end_date="today")
                if random.random() < 0.10 else ""
            )
            row = [
                member_id,
                fake.ssn(),
                fake.first_name(),
                fake.last_name(),
                fake.date_of_birth(minimum_age=18, maximum_age=85).isoformat(),
                random.choice(["M", "F"]),
                fake.email(),
                fake.phone_number(),
                fake.street_address(),
                fake.city(),
                fake.state_abbr(),
                fake.zipcode(),
                random.choice(PLAN_TYPES),
                enrollment.isoformat(),
                termination if termination else "",
                fake.date_time_between(start_date="-1y").isoformat(),
            ]
            w.writerow(row)
            members.append(member_id)

        # DELIBERATE DQ ISSUE #1: 5 duplicate member rows
        # The Day 2 DQ framework catches these via QUALIFY ROW_NUMBER() = 1.
        for _ in range(5):
            dup_id = random.choice(members)
            w.writerow([
                dup_id, fake.ssn(), fake.first_name(), fake.last_name(),
                fake.date_of_birth().isoformat(), "M", fake.email(),
                fake.phone_number(), fake.street_address(), fake.city(),
                fake.state_abbr(), fake.zipcode(), random.choice(PLAN_TYPES),
                "2024-01-01", "", fake.date_time().isoformat(),
            ])

    print(f"  -> {path.name}")
    return members


# ---------------------------------------------------------------------
# 2. Providers (2,000) — CSV
# ---------------------------------------------------------------------
def generate_providers():
    print(f"Generating {N_PROVIDERS:,} providers...")
    providers = []
    path = OUTPUT_DIR / "providers.csv"

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        w.writerow([
            "provider_id", "npi", "first_name", "last_name",
            "specialty", "network_status",
            "address_line1", "city", "state", "zip",
            "credentialed_date",
        ])
        for i in range(N_PROVIDERS):
            provider_id = f"P{i+1:06d}"
            # DELIBERATE DQ ISSUE #2: 3 providers with invalid NPI (wrong length)
            npi = "12345" if i < 3 else generate_npi()
            w.writerow([
                provider_id, npi,
                fake.first_name(), fake.last_name(),
                random.choice(SPECIALTIES),
                random.choice(NETWORK_STATUSES),
                fake.street_address(), fake.city(),
                fake.state_abbr(), fake.zipcode(),
                fake.date_between(start_date="-15y", end_date="-1y").isoformat(),
            ])
            providers.append(provider_id)

    print(f"  -> {path.name}")
    return providers


# ---------------------------------------------------------------------
# 3. Claims (500,000, partitioned by service_date) — CSV
# ---------------------------------------------------------------------
def generate_claims(members, providers):
    print(f"Generating {N_CLAIMS:,} claims across {CLAIM_DAYS} daily partitions...")
    per_day = N_CLAIMS // CLAIM_DAYS

    for day_offset in range(CLAIM_DAYS):
        service_date = CLAIM_END_DATE - timedelta(days=CLAIM_DAYS - 1 - day_offset)
        path = OUTPUT_DIR / f"claims_{service_date.isoformat()}.csv"

        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
            w.writerow([
                "claim_id", "member_id", "provider_id", "service_date",
                "claim_status", "icd10_diagnosis_code", "cpt_procedure_code",
                "billed_amount", "allowed_amount", "paid_amount",
                "patient_responsibility",
                "claim_received_date", "claim_processed_date",
            ])
            for _ in range(per_day):
                billed = round(random.uniform(80, 2500), 2)
                allowed = round(billed * random.uniform(0.35, 0.75), 2)
                status = random.choice(CLAIM_STATUSES)
                paid = round(allowed * 0.85, 2) if status == "PAID" else 0.0
                patient_resp = round(allowed - paid, 2)
                processed = (
                    (service_date + timedelta(days=random.randint(3, 21))).isoformat()
                    if status in ("PAID", "DENIED") else ""
                )

                # DELIBERATE DQ ISSUE #3: ~12 claims per day with nulls in critical fields
                if random.random() < 0.000024:  # ~12 across all days
                    member_id = ""
                else:
                    member_id = random.choice(members)

                w.writerow([
                    str(uuid.uuid4()),
                    member_id,
                    random.choice(providers),
                    service_date.isoformat(),
                    status,
                    random.choice(ICD10_CODES)[0],
                    random.choice(CPT_CODES)[0],
                    billed, allowed, paid, patient_resp,
                    (service_date + timedelta(days=random.randint(1, 5))).isoformat(),
                    processed,
                ])
        print(f"  -> {path.name} ({per_day:,} rows)")


# ---------------------------------------------------------------------
# 4. Pharmacy fills (100,000) — NDJSON, one nested array (refills)
# ---------------------------------------------------------------------
def generate_pharmacy_fills(members):
    print(f"Generating {N_PHARMACY_FILLS:,} pharmacy fills (NDJSON)...")
    path = OUTPUT_DIR / "pharmacy_fills.json"

    with open(path, "w", encoding="utf-8") as f:
        for _ in range(N_PHARMACY_FILLS):
            ncpdp, pharmacy_name = random.choice(PHARMACIES)
            ndc, drug_name = random.choice(DRUGS)
            fill_date = fake.date_between(start_date="-90d", end_date="today")
            n_refills = random.randint(0, 5)
            ingredient = round(random.uniform(5, 400), 2)
            copay = round(min(ingredient, random.uniform(5, 50)), 2)

            record = {
                "fill_id": str(uuid.uuid4()),
                "member_id": random.choice(members),
                "fill_date": fill_date.isoformat(),
                "pharmacy": {
                    "ncpdp_id": ncpdp,
                    "name": pharmacy_name,
                    "address": {
                        "city": fake.city(),
                        "state": fake.state_abbr(),
                        "zip": fake.zipcode(),
                    },
                },
                "prescription": {
                    "ndc_code": ndc,
                    "drug_name": drug_name,
                    "prescriber_npi": generate_npi(),
                    "days_supply": random.choice([30, 60, 90]),
                    "quantity": random.choice([30, 60, 90, 100]),
                    "refills": [
                        {
                            "refill_number": r + 1,
                            "fill_date": (
                                fill_date + timedelta(days=30 * (r + 1))
                            ).isoformat(),
                            "days_supply": 30,
                        }
                        for r in range(n_refills)
                    ],
                },
                "costs": {
                    "ingredient_cost": ingredient,
                    "copay": copay,
                    "plan_paid": round(ingredient - copay, 2),
                },
            }
            f.write(json.dumps(record) + "\n")

    print(f"  -> {path.name}")


# ---------------------------------------------------------------------
# 5. Clinical events (50,000) — NDJSON, multiple nested arrays
# ---------------------------------------------------------------------
def generate_clinical_events(members):
    print(f"Generating {N_CLINICAL_EVENTS:,} clinical events (NDJSON)...")
    path = OUTPUT_DIR / "clinical_events.json"

    with open(path, "w", encoding="utf-8") as f:
        for _ in range(N_CLINICAL_EVENTS):
            n_obs = random.randint(2, 5)
            n_dx = random.randint(1, 3)
            event_ts = fake.date_time_between(start_date="-180d")
            height = round(random.uniform(150, 195), 1)
            weight = round(random.uniform(50, 130), 1)

            record = {
                "event_id": str(uuid.uuid4()),
                "member_id": random.choice(members),
                "event_timestamp": event_ts.isoformat(),
                "encounter": {
                    "type": random.choice(ENCOUNTER_TYPES),
                    "provider_npi": generate_npi(),
                    "facility": fake.company() + " Medical Center",
                },
                "observations": [
                    {
                        "loinc_code": loinc,
                        "name": name,
                        "value": round(random.uniform(*rng), 1),
                        "unit": unit,
                        "recorded_at": event_ts.isoformat(),
                    }
                    for loinc, name, rng, unit in random.sample(
                        LOINC_OBSERVATIONS, k=n_obs
                    )
                ],
                "diagnoses": [
                    {
                        "icd10": random.choice(ICD10_CODES)[0],
                        "description": random.choice(ICD10_CODES)[1],
                        "primary": i == 0,
                    }
                    for i in range(n_dx)
                ],
                "vitals": {
                    "height_cm": height,
                    "weight_kg": weight,
                    "bmi": round(weight / ((height / 100) ** 2), 1),
                },
            }
            f.write(json.dumps(record) + "\n")

    print(f"  -> {path.name}")


def main():
    start = datetime.now()
    print("=" * 60)
    print("Healthcare Analytics Platform — Synthetic Data Generator")
    print("=" * 60)

    members = generate_members()
    providers = generate_providers()
    generate_claims(members, providers)
    generate_pharmacy_fills(members)
    generate_clinical_events(members)

    elapsed = (datetime.now() - start).total_seconds()
    print("=" * 60)
    print(f"Done in {elapsed:.1f}s. Files in: {OUTPUT_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
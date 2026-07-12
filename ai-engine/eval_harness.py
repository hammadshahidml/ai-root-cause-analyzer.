import os
import json
import sys

from diagnose import diagnose_incident

# ---------- Config ----------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INCIDENTS_DIR = os.path.join(SCRIPT_DIR, "..", "log-collector", "incidents")
GROUND_TRUTH_PATH = os.path.join(INCIDENTS_DIR, "ground_truth.json")


def load_ground_truth():
    with open(GROUND_TRUTH_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def check_root_cause_match(diagnosis: dict, keywords: list) -> bool:
    """Case-insensitive substring match against root_cause + reasoning text."""
    text = (
        str(diagnosis.get("root_cause", "")) + " " +
        str(diagnosis.get("reasoning", ""))
    ).lower()
    return any(kw.lower() in text for kw in keywords)


def check_confidence_match(diagnosis: dict, expected: str) -> bool:
    actual = str(diagnosis.get("confidence", "")).lower().strip()
    return actual == expected.lower().strip()


def shorten(name: str, length: int = 30) -> str:
    if len(name) <= length:
        return name
    return name[:length - 3] + "..."


def main():
    ground_truth = load_ground_truth()

    results = []
    for filename, expectation in ground_truth.items():
        incident_path = os.path.join(INCIDENTS_DIR, filename)
        if not os.path.isfile(incident_path):
            print(f"WARNING: incident file not found, skipping: {filename}")
            continue

        diagnosis = diagnose_incident(incident_path)

        if "error" in diagnosis:
            results.append({
                "file": filename,
                "root_cause_pass": False,
                "confidence_pass": False,
                "actual_root_cause": f"ERROR: {diagnosis.get('error')}",
                "actual_confidence": "N/A",
            })
            continue

        root_cause_pass = check_root_cause_match(
            diagnosis, expectation["expected_root_cause_keywords"]
        )
        confidence_pass = check_confidence_match(
            diagnosis, expectation["expected_confidence"]
        )

        results.append({
            "file": filename,
            "root_cause_pass": root_cause_pass,
            "confidence_pass": confidence_pass,
            "actual_root_cause": diagnosis.get("root_cause", ""),
            "actual_confidence": diagnosis.get("confidence", ""),
        })

    # ---------- Print results table ----------
    print()
    print(f"{'Incident':32} {'RootCause':10} {'Confidence':11} {'Actual Root Cause':40} {'Actual Conf.'}")
    print("-" * 110)
    for r in results:
        rc_status = "PASS" if r["root_cause_pass"] else "FAIL"
        conf_status = "PASS" if r["confidence_pass"] else "FAIL"
        print(
            f"{shorten(r['file'], 32):32} "
            f"{rc_status:10} "
            f"{conf_status:11} "
            f"{shorten(str(r['actual_root_cause']), 40):40} "
            f"{r['actual_confidence']}"
        )

    total = len(results)
    rc_pass_count = sum(1 for r in results if r["root_cause_pass"])
    conf_pass_count = sum(1 for r in results if r["confidence_pass"])

    print("-" * 110)
    print(f"Summary: {rc_pass_count}/{total} root cause matches, {conf_pass_count}/{total} confidence matches")
    print()


if __name__ == "__main__":
    main()

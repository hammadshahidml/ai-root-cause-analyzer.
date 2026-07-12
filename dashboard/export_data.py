import json
import os
import glob

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INCIDENTS_DIR = os.path.join(SCRIPT_DIR, "..", "log-collector", "incidents")
OUTPUT_PATH = os.path.join(SCRIPT_DIR, "data.json")


def main():
    records = []
    for filepath in glob.glob(os.path.join(INCIDENTS_DIR, "*_diagnosis.json")):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["_filename"] = os.path.basename(filepath)
            records.append(data)
        except Exception as e:
            print(f"Skipping {filepath}: {e}")

    records.sort(key=lambda r: r.get("detected_at", ""), reverse=True)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)

    print(f"Exported {len(records)} diagnosed incidents to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()

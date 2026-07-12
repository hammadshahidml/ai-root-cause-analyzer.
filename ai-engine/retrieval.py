import os
import json
import glob
from difflib import SequenceMatcher
from typing import Dict, Any, List


def _similarity(a: str, b: str) -> float:
    """Simple text similarity ratio between two strings, 0.0-1.0."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _load_past_incidents(incidents_folder: str, exclude_id: str = None) -> List[Dict[str, Any]]:
    """Load all *_diagnosis.json files from the incidents folder."""
    pattern = os.path.join(incidents_folder, "*_diagnosis.json")
    results = []
    for filepath in glob.glob(pattern):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if exclude_id and data.get("incident_id") == exclude_id:
            continue
        results.append(data)
    return results


def get_similar_incidents(
    current_incident: Dict[str, Any],
    incidents_folder: str,
    top_k: int = 2,
) -> List[Dict[str, Any]]:
    """
    Find the top_k most similar past incidents (with diagnosis) to the
    current incident, based on simple text similarity of the trigger line.

    Returns a list of past incident dicts (each including its prior
    diagnosis fields, if present). Excludes the current incident itself.
    """
    current_id = current_incident.get("incident_id")
    current_trigger = current_incident.get("trigger_line", "")

    past_incidents = _load_past_incidents(incidents_folder, exclude_id=current_id)
    if not past_incidents:
        return []

    scored = []
    for past in past_incidents:
        past_trigger = past.get("trigger_line", "")
        score = _similarity(current_trigger, past_trigger)
        scored.append((score, past))

    # Sort by similarity score, descending
    scored.sort(key=lambda x: x[0], reverse=True)

    # Only keep results with a non-trivial similarity (avoid noise from
    # completely unrelated incidents)
    filtered = [past for score, past in scored if score > 0.15]

    return filtered[:top_k]
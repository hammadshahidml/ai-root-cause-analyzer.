import os
import sys
import json
import uuid
import textwrap
from typing import Dict, Any

from retrieval import get_similar_incidents

# ---------- LLM Provider Abstraction ----------

def call_llm(system_prompt: str, user_content: str) -> str:
    """Call the Groq LLM and return the raw response string.
    This function isolates the provider so it can be swapped later.
    """
    try:
        from groq import Groq
    except ImportError:
        raise RuntimeError("Groq SDK not installed. Install via 'pip install groq'.")

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("Environment variable GROQ_API_KEY not set.")
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    client = Groq(api_key=api_key)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.0,
        max_tokens=1024,
    )
    return response.choices[0].message.content

# ---------- Prompt Construction ----------


def build_user_content(incident: Dict[str, Any], similar_incidents: list) -> str:
    """Build the user message, embedding the current incident plus any
    similar past incidents (with their prior diagnosis) as reference-only
    context. Trims oversized incidents to stay within LLM token limits.
    """
    trimmed_incident = dict(incident)
    logs = trimmed_incident.get("logs", [])
    max_logs = 40
    if len(logs) > max_logs:
        trimmed_incident["logs"] = logs[:max_logs]
        trimmed_incident["_note"] = (
            f"Log list truncated: {len(logs)} lines captured, "
            f"showing first {max_logs}."
        )

    payload = {
        "current_incident": trimmed_incident,
    }
    if similar_incidents:
        payload["past_similar_incidents_for_reference_only"] = similar_incidents
    return json.dumps(payload, ensure_ascii=False)


def build_system_prompt() -> str:
    return textwrap.dedent("""
    You are an expert root‑cause analyst. Classify the trigger error as either
    "self-explanatory" (the root cause is clear from the error message alone)
    or "ambiguous" (the error needs surrounding log context to determine the
    cause).

    IMPORTANT: Network and connectivity-related errors (e.g., DNS resolution
    failures, "connection refused", "connection closed", "timeout", "could not
    translate host name") are NEVER self-explanatory by default, even if the
    error message sounds specific. These errors can have multiple distinct
    root causes: a real DNS misconfiguration, a dependency container being
    down or restarted, a network partition, a typo in a hostname, or a
    firewall rule. Before calling such an error "self-explanatory" or
    assigning high confidence, you MUST find explicit log evidence (e.g., a
    shutdown message, a crash, a restart event) that confirms the SPECIFIC
    cause. If no such evidence exists in the provided logs, classify the
    error as "ambiguous" and set confidence to "low" or "insufficient_evidence"
    instead of defaulting to a generic fix like "verify DNS configuration"
    when the real cause could be something else entirely.

    Only treat an error as genuinely self-explanatory if it names the exact
    problem with no other plausible interpretation (e.g., "authentication
    failed: wrong password", "disk full", "out of memory: killed process").

    You may be given past similar incidents with their prior diagnosis, under
    the key "past_similar_incidents_for_reference_only". Treat these as
    reference evidence only, not as ground truth. Re-evaluate the current
    incident independently using the current incident's own logs. If the past
    diagnosis conflicts with what the current logs show, set "conflict_flag"
    to describe the discrepancy. Do not copy a past diagnosis's root_cause or
    confidence just because it matches a similar error string — verify it
    against the current evidence first.

    Apply the following confidence logic:
    * high confidence = self‑explanatory error, OR ambiguous error with clear supporting
      context in the logs;
    * low confidence = ambiguous error with no supporting context;
    * If the error message and surrounding context conflict, set the
      "conflict_flag" accordingly.
    Never guess a root cause without evidence from the logs.
    Respond ONLY with a JSON object matching this schema (no markdown, no preamble):
    {
      "root_cause": "string",
      "confidence": "high | medium | low | insufficient_evidence",
      "reasoning": "string, 2-3 sentences max",
      "evidence": ["short excerpts or line references from logs"],
      "conflict_flag": "string or null, describes any contradiction found",
      "suggested_fix": "string"
    }
    """)

# ---------- Response Parsing ----------

def safe_parse_response(response_text: str) -> Dict[str, Any]:
    """Extract JSON from the LLM response, handling optional markdown fences."""
    if response_text.strip().startswith('```'):
        lines = response_text.strip().splitlines()
        if lines[0].startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].startswith('```'):
            lines = lines[:-1]
        response_text = "\n".join(lines)
    try:
        return json.loads(response_text)
    except json.JSONDecodeError as e:
        return {
            "error": "JSON parse error",
            "exception": str(e),
            "raw_response": response_text,
        }

# ---------- Persistence ----------

def save_diagnosis(incident: Dict[str, Any], diagnosis: Dict[str, Any], incidents_folder: str) -> str:
    """Save the incident + diagnosis together so future runs can retrieve
    it as a 'past incident with diagnosis' via retrieval.py.
    """
    incident_id = incident.get("incident_id", str(uuid.uuid4()))
    combined = dict(incident)
    combined.update(diagnosis)
    combined["incident_id"] = incident_id

    filename = f"incident_{incident_id}_diagnosis.json"
    filepath = os.path.join(incidents_folder, filename)
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(combined, f, indent=2)
    except Exception as e:
        print(f"Warning: failed to save diagnosis record: {e}")
    return filepath

# ---------- Core Diagnosis Function ----------

def diagnose_incident(incident_json_path: str) -> Dict[str, Any]:
    """Read an incident JSON file, retrieve similar past incidents, query
    the LLM, and return the parsed diagnosis. Also persists the result.
    """
    if not os.path.isfile(incident_json_path):
        return {"error": f"File not found: {incident_json_path}"}
    try:
        with open(incident_json_path, "r", encoding="utf-8") as f:
            incident = json.load(f)
    except Exception as e:
        return {"error": "Failed to read incident JSON", "exception": str(e)}

    incidents_folder = os.path.dirname(os.path.abspath(incident_json_path))

    similar_incidents = get_similar_incidents(incident, incidents_folder, top_k=2)

    system_prompt = build_system_prompt()
    user_content = build_user_content(incident, similar_incidents)
    try:
        raw_response = call_llm(system_prompt, user_content)
    except Exception as e:
        return {"error": "LLM call failed", "exception": str(e)}

    parsed = safe_parse_response(raw_response)

    if "error" not in parsed:
        save_diagnosis(incident, parsed, incidents_folder)

    return parsed

# ---------- CLI Helper ----------

def pretty_print_diagnosis(diagnosis: Dict[str, Any]):
    if "error" in diagnosis:
        print("Error:", diagnosis.get("error"))
        if "exception" in diagnosis:
            print("Details:", diagnosis["exception"])
        return
    print("Root Cause:", diagnosis.get("root_cause"))
    print("Confidence:", diagnosis.get("confidence"))
    print("Reasoning:", diagnosis.get("reasoning"))
    print("Evidence:")
    for ev in diagnosis.get("evidence", []):
        print(f"  - {ev}")
    conflict = diagnosis.get("conflict_flag")
    if conflict:
        print("Conflict Flag:", conflict)
    print("Suggested Fix:", diagnosis.get("suggested_fix"))

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python diagnose.py <incident_json_path>")
        sys.exit(1)
    incident_path = sys.argv[1]
    result = diagnose_incident(incident_path)
    pretty_print_diagnosis(result)
    
import os, json, time, uuid, queue, threading, docker
from collections import deque
from datetime import datetime, timezone

# Directory to save incident files
incidents_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "incidents")
os.makedirs(incidents_dir, exist_ok=True)

log_queue = queue.Queue()
active_streams, active_incidents = {}, []
history_buffer = deque()
latest_ts = None

# FIX: Deduplication — tracks the last time each (container, error signature)
# combination triggered a new incident. If the same signature repeats within
# DEDUP_WINDOW_SECONDS, we treat it as part of the SAME ongoing failure
# (e.g. a crash-restart loop) instead of creating a new incident file.
DEDUP_WINDOW_SECONDS = 20.0
recent_triggers = {}  # (container, signature) -> last_trigger_datetime


import re

def error_signature(message: str) -> str:
    """Build a short, stable signature for an error message so repeated
    occurrences of the SAME underlying error can be recognized, even when
    timestamps, PIDs, or other numeric details differ between occurrences.

    FIX: the first version used message[:80] directly, but log lines like
    postgres's often embed a changing PID/timestamp right at the start
    (e.g. "[75] FATAL: ..." vs "[82] FATAL: ..."), so every occurrence got
    a different signature and deduplication never matched. Stripping all
    digits first removes that variability while keeping the actual error
    text intact.
    """
    normalized = re.sub(r'\d+', '', message)
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized[:100]


def parse_timestamp(ts_str):
    if not ts_str: 
        return datetime.now(timezone.utc)
    try:
        if ts_str.endswith('Z'): 
            ts_str = ts_str[:-1] + '+00:00'
        if '.' in ts_str:
            base, frac = ts_str.split('.', 1)
            import re
            m = re.match(r'^(\d+)(.*)$', frac)
            if m: 
                ts_str = f"{base}.{m.group(1)[:6]}{m.group(2)}"
        return datetime.fromisoformat(ts_str)
    except Exception:
        return datetime.now(timezone.utc)

def stream_container_logs(container):
    try:
        # Stream container logs from now (tail=0)
        for line in container.logs(stream=True, timestamps=True, tail=0):
            log_queue.put((container.name, line))
    except Exception: 
        pass

def finalize_incident(inc):
    inc["logs"].sort(key=lambda x: x["timestamp"])
    # Format a safe filename timestamp for Windows
    ts_safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in inc["detected_at"])
    filename = f"incident_{ts_safe}.json"
    filepath = os.path.join(incidents_dir, filename)
    data = {
        "incident_id": inc["incident_id"],
        "detected_at": inc["detected_at"],
        "trigger_line": inc["trigger_line"],
        "trigger_container": inc["trigger_container"],
        "trigger_reason": inc.get("trigger_reason", "error_log"),
        "occurrence_count": inc.get("occurrence_count", 1),
        "logs": [{"container": e["container"], "timestamp": e["timestamp"], "line": e["line"]} for e in inc["logs"]]
    }
    # Atomic write: temp file first, then rename, to avoid corruption when
    # multiple incidents finalize in rapid succession.
    try:
        tmp_filepath = filepath + f".{uuid.uuid4().hex}.tmp"
        with open(tmp_filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_filepath, filepath)
        occ = data["occurrence_count"]
        occ_note = f" (seen {occ}x, deduplicated)" if occ > 1 else ""
        print(f"Incident captured: incidents/{filename}{occ_note}")
    except Exception as e:
        print(f"Error saving: {e}")

def main():
    global latest_ts, active_incidents
    client = docker.from_env()
    print("Log collector started. Watching containers in project 'airca'...")
    last_check = 0
    while True:
        now = time.time()
        # Scan for container additions/restarts every 5 seconds
        if now - last_check > 5.0:
            try:
                containers = client.containers.list(filters={"label": "com.docker.compose.project=airca", "status": "running"})
                for c in containers:
                    if c.id not in active_streams or not active_streams[c.id].is_alive():
                        t = threading.Thread(target=stream_container_logs, args=(c,), daemon=True)
                        t.start()
                        active_streams[c.id] = t
            except Exception as e:
                print(f"Error scanning containers: {e}")
            last_check = now

        try:
            container_name, line_bytes = log_queue.get(timeout=1.0)
        except queue.Empty:
            # Check real-world time timeout fallback when queue is idle
            now_real, still_active = datetime.now(timezone.utc), []
            for inc in active_incidents:
                if (now_real - inc["real_trigger_time"]).total_seconds() > 32.0: 
                    finalize_incident(inc)
                else: 
                    still_active.append(inc)
            active_incidents = still_active
            continue

        line = line_bytes.decode('utf-8', errors='ignore').rstrip('\r\n')
        timestamp_str, message = line.split(' ', 1) if ' ' in line else ("", line)
        t_log = parse_timestamp(timestamp_str)
        log_entry = {"container": container_name, "timestamp": timestamp_str or t_log.isoformat(), "line": message}
        
        history_buffer.append((t_log, log_entry))
        if latest_ts is None or t_log > latest_ts: 
            latest_ts = t_log
        
        # Prune logs older than 30 seconds from history buffer
        while history_buffer and (latest_ts - history_buffer[0][0]).total_seconds() > 30.0:
            history_buffer.popleft()

        # Add log to currently active incident windows
        for inc in active_incidents:
            if (t_log - inc["trigger_time"]).total_seconds() <= 30.0:
                inc["logs"].append(log_entry)

        # Trigger logic
        is_trigger = False
        trigger_reason = None
        try:
            json_data = json.loads(message)
            if isinstance(json_data, dict):
                lvl = str(json_data.get("level", "")).upper()
                msg_text = str(json_data.get("message", ""))
                if lvl in ("ERROR", "FATAL"):
                    is_trigger = True
                    trigger_reason = "error_log"
                # FIX: latency detection gap — watch for the SLOW_REQUEST
                # marker logged by order-service's middleware, since a slow
                # but successful request never produces an ERROR/FATAL line.
                elif "SLOW_REQUEST" in msg_text:
                    is_trigger = True
                    trigger_reason = "slow_request"
        except Exception:
            if "ERROR" in message or "FATAL" in message:
                is_trigger = True
                trigger_reason = "error_log"
            elif "SLOW_REQUEST" in message:
                is_trigger = True
                trigger_reason = "slow_request"

        if is_trigger:
            now_real = datetime.now(timezone.utc)
            sig = error_signature(message)
            dedup_key = (container_name, sig)

            last_seen = recent_triggers.get(dedup_key)
            is_duplicate = (
                last_seen is not None
                and (now_real - last_seen).total_seconds() <= DEDUP_WINDOW_SECONDS
            )

            if is_duplicate:
                # FIX: Same error signature from the same container seen
                # recently — treat as part of the SAME ongoing failure
                # instead of creating a new incident. Bump the occurrence
                # count on the matching active incident, if still open.
                recent_triggers[dedup_key] = now_real
                for inc in active_incidents:
                    if inc["trigger_container"] == container_name and inc.get("signature") == sig:
                        inc["occurrence_count"] = inc.get("occurrence_count", 1) + 1
                        break
            else:
                recent_triggers[dedup_key] = now_real
                new_inc = {
                    "incident_id": str(uuid.uuid4()),
                    "detected_at": timestamp_str or t_log.isoformat(),
                    "trigger_time": t_log,
                    "real_trigger_time": now_real,
                    "trigger_line": message,
                    "trigger_container": container_name,
                    "trigger_reason": trigger_reason,
                    "signature": sig,
                    "occurrence_count": 1,
                    "logs": [e for _, e in history_buffer]
                }
                active_incidents.append(new_inc)

        # Close incidents that are older than 30 seconds
        now_real, still_active = datetime.now(timezone.utc), []
        for inc in active_incidents:
            time_stream = (latest_ts - inc["trigger_time"]).total_seconds()
            time_real = (now_real - inc["real_trigger_time"]).total_seconds()
            if time_stream > 30.0 or time_real > 32.0:
                finalize_incident(inc)
            else:
                still_active.append(inc)
        active_incidents = still_active

if __name__ == "__main__":
    try: 
        main()
    except KeyboardInterrupt: 
        print("\nExiting...")

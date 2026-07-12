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
        "logs": [{"container": e["container"], "timestamp": e["timestamp"], "line": e["line"]} for e in inc["logs"]]
    }
    # FIX: write to a temp file first, then atomically rename it into place.
    # This prevents corrupted/interleaved files when multiple incidents
    # finalize in rapid succession (e.g. during a crash loop).
    try:
        tmp_filepath = filepath + f".{uuid.uuid4().hex}.tmp"
        with open(tmp_filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_filepath, filepath)
        print(f"Incident captured: incidents/{filename}")
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
        try:
            json_data = json.loads(message)
            if isinstance(json_data, dict) and str(json_data.get("level", "")).upper() in ("ERROR", "FATAL"):
                is_trigger = True
        except Exception:
            if "ERROR" in message or "FATAL" in message:
                is_trigger = True

        if is_trigger:
            new_inc = {
                "incident_id": str(uuid.uuid4()),
                "detected_at": timestamp_str or t_log.isoformat(),
                "trigger_time": t_log,
                "real_trigger_time": datetime.now(timezone.utc),
                "trigger_line": message,
                "trigger_container": container_name,
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

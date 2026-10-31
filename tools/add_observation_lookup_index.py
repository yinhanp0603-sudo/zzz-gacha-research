"""现有入库任务的有界索引补充：任何锁等待/建索引过久均回滚退出，不终止导入任务。"""
import json
import sqlite3
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
db = root / "outputs/benchmark_n200/observations/observations.sqlite"
conn = sqlite3.connect(db, timeout=0.1, isolation_level=None)
started = time.perf_counter()
try:
    for attempt in range(100):
        try:
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA cache_size=-65536")
            conn.execute("BEGIN IMMEDIATE")
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) or attempt == 99:
                raise
            time.sleep(0.1)
    deadline = time.perf_counter() + 3.0
    conn.set_progress_handler(lambda: time.perf_counter() > deadline, 10000)
    conn.execute("CREATE INDEX IF NOT EXISTS ix_obs_build_identity ON obs_build(snapshot_id,local_uid)")
    conn.execute("COMMIT")
    result = {"status": "created", "elapsed_seconds": time.perf_counter() - started}
except sqlite3.OperationalError as exc:
    conn.set_progress_handler(None, 0)
    if conn.in_transaction:
        conn.execute("ROLLBACK")
    result = {"status": "deferred", "reason": str(exc), "elapsed_seconds": time.perf_counter() - started}
finally:
    conn.close()
target = root / "outputs/benchmark_n200/verification/observation_index_patch.json"
target.write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result))

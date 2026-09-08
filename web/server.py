"""DELIVERABLE HTTP API + UI host."""

from __future__ import annotations

import json
import sys
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.pipeline import run_pipeline  # noqa: E402
from qc import archive, store  # noqa: E402

app = FastAPI(title="DELIVERABLE", version="1.0")

WORK = Path("/tmp/deliverable-runs")
WORK.mkdir(parents=True, exist_ok=True)
WEB = Path(__file__).parent

_runs: dict[str, dict] = {}
_lock = threading.Lock()

# --- ClickHouse readiness flag (background boot in Cloud Run) ----------------

_ch_ready = threading.Event()
_ch_error: str | None = None


def _wait_for_clickhouse():
    """Background thread: poll local ClickHouse until it responds, then set flag."""
    import os
    import time

    if os.getenv("CLICKHOUSE_HOST", "localhost") != "localhost":
        # External CH (ClickHouse Cloud) -- should already be reachable
        try:
            store.client().query("SELECT 1")
            _ch_ready.set()
        except Exception as exc:
            global _ch_error
            _ch_error = str(exc)[:200]
        return

    for _ in range(120):
        try:
            store.client().query("SELECT 1")
            _ch_ready.set()
            return
        except Exception:
            time.sleep(1)
    global _ch_error
    _ch_error = "ClickHouse did not become reachable within 120s"


# Start the readiness poller as soon as the module loads
threading.Thread(target=_wait_for_clickhouse, daemon=True).start()


@app.get("/", response_class=HTMLResponse)
def index():
    return (WEB / "index.html").read_text()


@app.get("/api/health")
def health():
    if not _ch_ready.is_set():
        msg = _ch_error or "ClickHouse is starting up"
        return JSONResponse({"ok": False, "warming": True, "detail": msg}, status_code=503)
    try:
        ch = store.client()
        version = ch.query("SELECT version()").result_rows[0][0]
        return {"ok": True, "clickhouse": version}
    except Exception as exc:
        return JSONResponse({"ok": False, "clickhouse_error": str(exc)[:200]}, status_code=503)


@app.get("/api/titles")
def titles(rows: int = 12):
    """Public-domain titles available to QC, straight from archive.org."""
    found = archive.search(rows=rows)
    out = []
    for doc in found:
        try:
            out.append(archive.pick_files(doc["identifier"]))
        except Exception:
            continue
    return {"titles": [t for t in out if t["video"]]}


@app.get("/api/catalog")
def catalog():
    if not _ch_ready.is_set():
        return JSONResponse({"catalog": [], "warming": True}, status_code=200)
    try:
        return {"catalog": store.catalog()}
    except Exception as exc:
        raise HTTPException(503, f"clickhouse unavailable: {exc}")


@app.post("/api/run/{identifier}")
def start_run(identifier: str, seconds: int = 180, max_bytes: int = 16_000_000):
    """Kick off a QC pass. Returns a job id to poll."""
    if not _ch_ready.is_set():
        raise HTTPException(503, "ClickHouse is still starting up, try again shortly")

    job = str(uuid.uuid4())[:8]
    with _lock:
        _runs[job] = {"state": "running", "identifier": identifier, "steps": []}

    def _work():
        try:
            run = run_pipeline(identifier, WORK / job, seconds=seconds, max_bytes=max_bytes)
            with _lock:
                _runs[job] = {"state": "done", "identifier": identifier, **run.as_dict()}
        except Exception as exc:  # surfaced to the UI, never swallowed
            with _lock:
                _runs[job] = {"state": "failed", "identifier": identifier, "error": str(exc)[:400]}

    threading.Thread(target=_work, daemon=True).start()
    return {"job": job}


@app.get("/api/run/{job}")
def get_run(job: str):
    with _lock:
        if job not in _runs:
            raise HTTPException(404, "no such job")
        return _runs[job]


@app.get("/api/audio/{job}/{stage}")
def audio(job: str, stage: str):
    """The actual before/after audio, so the defect is audible in the demo."""
    d = WORK / job
    path = d / ("source.mp4" if stage == "before" else "fixed.m4a")
    if not path.exists():
        raise HTTPException(404, "not rendered yet")
    return FileResponse(path)


if __name__ == "__main__":
    import uvicorn
    import os
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

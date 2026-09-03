"""Unified local server for the 01.AI interactive website and full Solar Inspection demo."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
SOLAR_DIR = ROOT / "demos" / "solar-inspection"
sys.path.insert(0, str(SOLAR_DIR))

import server as solar_server  # noqa: E402

app = FastAPI(title="01.AI Interactive Website")

_solar_started = False

@app.on_event("startup")
async def start_solar_runtime() -> None:
    """Mounted FastAPI sub-apps do not run their startup handlers under this deployment."""
    global _solar_started
    if not _solar_started:
        await solar_server.startup()
        _solar_started = True

app.mount("/demos/solar-inspection", solar_server.app)
app.mount("/", StaticFiles(directory=str(ROOT), html=True), name="website")

if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8774"))
    uvicorn.run(app, host=host, port=port)

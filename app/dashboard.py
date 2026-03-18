"""FastAPI dashboard for visualizing and scheduling pipeline runs."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .dashboard_runtime import DashboardRunManager


def _project_root() -> Path:
    env_root = os.getenv("MACROSCOPE_UI_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return Path(__file__).resolve().parent.parent


MANAGER = DashboardRunManager(_project_root())
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    MANAGER.start()
    try:
        yield
    finally:
        MANAGER.shutdown()


app = FastAPI(title="Macroscope Pipeline Dashboard", lifespan=lifespan)
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).resolve().parent / "static")),
    name="static",
)


class RunRequest(BaseModel):
    """Payload for a manual pipeline run."""

    trigger: str = "manual"


class ScheduleRequest(BaseModel):
    """Payload for daily scheduling from the UI."""

    enabled: bool
    daily_time: str = Field(pattern=r"^([01]\d|2[0-3]):([0-5]\d)$")


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    """Render the single-page dashboard shell."""
    return TEMPLATES.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"request": request},
    )


@app.get("/api/status")
async def get_status() -> dict:
    """Return current dashboard status and recent runs."""
    return MANAGER.get_status()


@app.post("/api/runs/start")
async def start_run(payload: RunRequest) -> dict:
    """Kick off a new pipeline run in the background."""
    try:
        return MANAGER.start_run(trigger=payload.trigger)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/runs/stop")
async def stop_run() -> dict:
    """Request a cooperative stop for the active pipeline run."""
    try:
        return MANAGER.stop_run()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/runs/{run_id}/resume")
async def resume_run(run_id: str) -> dict:
    """Resume an interrupted run from its last checkpointed stage."""
    try:
        return MANAGER.resume_run(run_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    """Return a full view model for one run."""
    try:
        return MANAGER.get_run_detail(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/artifacts/{artifact_name:path}")
async def get_artifact(run_id: str, artifact_name: str) -> dict:
    """Return the contents of a run artifact for the viewer panel."""
    try:
        return MANAGER.get_artifact_content(run_id, artifact_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/download/{artifact_name:path}")
async def download_artifact(run_id: str, artifact_name: str) -> FileResponse:
    """Download a run artifact directly from the dashboard."""
    try:
        path = MANAGER.resolve_artifact_path(run_id, artifact_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return FileResponse(path=path, filename=path.name)


@app.post("/api/schedule")
async def update_schedule(payload: ScheduleRequest) -> dict:
    """Enable or disable the daily schedule."""
    return MANAGER.update_schedule(
        enabled=payload.enabled,
        daily_time=payload.daily_time,
    )

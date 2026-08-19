"""
app.py — FastAPI application.

Routes:
  GET  /                        Dashboard HTML
  GET  /api/accounts            List configured accounts
  POST /api/scan/{account_id}   Scan achievements for an account
  POST /api/plan/{account_id}   Build action plan
  GET  /api/achievements/{id}   Get cached achievement list
  POST /api/execute             Execute action plan (dry-run or live)
  POST /api/stop                Emergency stop
  POST /api/reset               Reset stop flag (user must confirm)
  GET  /api/status              Current state + budgets + rate limits
  GET  /api/log                 Audit log (paginated)
  GET  /api/stream/{account_id} SSE progress stream
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import database as db
import safety
from config import get_configured_accounts, get_token
from rate_limiter import get_rate_summary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    logger.info("GitHub Achievement Agent started.")
    yield
    logger.info("Shutting down.")


app = FastAPI(
    title="GitHub Achievement Agent",
    description="Legitimate GitHub achievement automation with safety controls.",
    version="1.0.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    accounts = get_configured_accounts()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "accounts": accounts,
            "stopped": safety.is_stopped(),
            "stop_reason": safety.get_stop_reason(),
        },
    )


# ---------------------------------------------------------------------------
# Account info
# ---------------------------------------------------------------------------

@app.get("/api/accounts")
async def list_accounts():
    accounts = get_configured_accounts()
    return {"accounts": accounts, "count": len(accounts)}


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

@app.post("/api/scan/{account_id}")
async def scan_achievements(account_id: int):
    if not get_token(account_id):
        raise HTTPException(404, f"No token configured for account {account_id}")

    from github_client import GitHubClient
    from achievements import scan_achievements as do_scan

    try:
        client = GitHubClient(account_id)
        results = await do_scan(account_id, client)
        return {"account_id": account_id, "achievements": results, "count": len(results)}
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        logger.error("Scan error: %s", e)
        raise HTTPException(500, str(e))


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

class PlanRequest(BaseModel):
    account_id: int
    second_account_id: int | None = None


@app.post("/api/plan")
async def build_plan(req: PlanRequest):
    from planner import build_action_plan, summarize_plan

    achievements = db.get_achievements(req.account_id)
    if not achievements:
        raise HTTPException(400, "No achievements cached. Run a scan first.")

    second_available = bool(
        req.second_account_id and get_token(req.second_account_id)
    )

    # Re-attach human hints from registry
    from achievements import ACHIEVEMENTS
    enriched = []
    for a in achievements:
        ach_def = ACHIEVEMENTS.get(a["name"])
        if ach_def:
            a["human_action_hint"] = ach_def.human_action_hint
            a["automation_class"] = ach_def.automation_class.value
            a["risk_level"] = ach_def.risk_level.value
            a["requires_second_account"] = ach_def.requires_second_account
            a["requires_payment"] = ach_def.requires_payment
            a["tiers"] = [{"name": t.name, "requirement": t.requirement} for t in ach_def.tiers]
            a["emoji"] = ach_def.emoji
            a["description"] = ach_def.description
            a["key"] = a["name"]
            a["display_name"] = ach_def.display_name
        enriched.append(a)

    plan = build_action_plan(enriched, second_account_available=second_available)
    summary = summarize_plan(plan)

    return {
        "account_id": req.account_id,
        "second_account_available": second_available,
        "plan": [
            {
                "achievement_key": p.achievement_key,
                "display_name": p.display_name,
                "emoji": p.emoji,
                "classification": p.classification,
                "status": p.status,
                "progress": p.progress,
                "risk_level": p.risk_level,
                "description": p.description,
                "human_hint": p.human_hint,
                "is_executable": p.is_executable,
                "requires_confirmation": p.requires_confirmation,
                "requires_second_account": p.requires_second_account,
                "requires_payment": p.requires_payment,
                "tiers": p.tiers,
            }
            for p in plan
        ],
        "summary": {
            "total": summary["total"],
            "counts": summary["counts"],
            "executable_count": len(summary["executable"]),
            "human_required_count": len(summary["human_required"]),
            "payment_required_count": len(summary["payment_required"]),
        },
    }


# ---------------------------------------------------------------------------
# Achievements cache
# ---------------------------------------------------------------------------

@app.get("/api/achievements/{account_id}")
async def get_achievements(account_id: int):
    achievements = db.get_achievements(account_id)
    return {"account_id": account_id, "achievements": achievements}


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

class ExecuteRequest(BaseModel):
    account_id: int
    second_account_id: int | None = None
    dry_run: bool = True
    achievement_keys: list[str] | None = None  # None = execute all eligible


_active_sessions: dict[int, asyncio.Task] = {}


@app.post("/api/execute")
async def execute(req: ExecuteRequest):
    if safety.is_stopped() and not req.dry_run:
        raise HTTPException(503, f"Automation stopped: {safety.get_stop_reason()}")

    from planner import build_action_plan
    from achievements import ACHIEVEMENTS

    achievements = db.get_achievements(req.account_id)
    if not achievements:
        raise HTTPException(400, "No achievements cached. Run a scan first.")

    second_available = bool(req.second_account_id and get_token(req.second_account_id))

    enriched = []
    for a in achievements:
        ach_def = ACHIEVEMENTS.get(a["name"])
        if ach_def:
            a["human_action_hint"] = ach_def.human_action_hint
            a["automation_class"] = ach_def.automation_class.value
            a["risk_level"] = ach_def.risk_level.value
            a["requires_second_account"] = ach_def.requires_second_account
            a["requires_payment"] = ach_def.requires_payment
            a["tiers"] = [{"name": t.name, "requirement": t.requirement} for t in ach_def.tiers]
            a["emoji"] = ach_def.emoji
            a["description"] = ach_def.description
            a["key"] = a["name"]
            a["display_name"] = ach_def.display_name
        enriched.append(a)

    plan = build_action_plan(enriched, second_account_available=second_available)

    # Filter by requested keys
    if req.achievement_keys:
        plan = [p for p in plan if p.achievement_key in req.achievement_keys]

    # Only executable actions
    executable = [p for p in plan if p.is_executable]

    if not executable:
        return {"message": "No executable actions in plan.", "results": []}

    # Run synchronously for simplicity (small number of actions)
    from executor import execute_plan
    results = []
    async for event in execute_plan(
        req.account_id,
        executable,
        dry_run=req.dry_run,
        second_account_id=req.second_account_id,
    ):
        results.append(event)

    return {"dry_run": req.dry_run, "results": results}


# ---------------------------------------------------------------------------
# SSE stream for real-time progress
# ---------------------------------------------------------------------------

@app.get("/api/stream/{account_id}")
async def stream_progress(account_id: int, request: Request):
    """Server-Sent Events stream for live execution progress."""

    async def event_generator() -> AsyncGenerator[str, None]:
        last_log_id = 0
        while True:
            if await request.is_disconnected():
                break

            logs = db.get_audit_log(limit=20)
            new_logs = [l for l in logs if l["id"] > last_log_id]
            if new_logs:
                last_log_id = max(l["id"] for l in new_logs)
                for log in reversed(new_logs):
                    yield f"data: {json.dumps(log)}\n\n"

            stopped = safety.is_stopped()
            if stopped:
                yield f"data: {json.dumps({'type': 'stopped', 'reason': safety.get_stop_reason()})}\n\n"

            await asyncio.sleep(2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Emergency stop / reset
# ---------------------------------------------------------------------------

@app.post("/api/stop")
async def emergency_stop():
    safety.trigger_emergency_stop("Manual stop requested by user.")
    db.log_action(0, "EMERGENCY STOP", "STOPPED", risk_status="HIGH", dry_run=False)
    return {"stopped": True, "reason": safety.get_stop_reason()}


@app.post("/api/reset")
async def reset_stop():
    safety.reset_stop()
    return {"stopped": False, "message": "Automation stop flag cleared."}


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@app.get("/api/status")
async def get_status():
    accounts = get_configured_accounts()
    rate_summaries = {}
    for acc in accounts:
        rate_summaries[str(acc)] = get_rate_summary(acc)

    return {
        "stopped": safety.is_stopped(),
        "stop_reason": safety.get_stop_reason(),
        "stop_time": safety.get_stop_time(),
        "accounts": accounts,
        "rate_limits": rate_summaries,
    }


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

@app.get("/api/log")
async def get_log(limit: int = 100, offset: int = 0):
    logs = db.get_audit_log(limit=limit, offset=offset)
    return {"logs": logs, "count": len(logs)}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    from config import APP_HOST, APP_PORT
    uvicorn.run("app:app", host=APP_HOST, port=APP_PORT, reload=True)

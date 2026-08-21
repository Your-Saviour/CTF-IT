"""Scenario CRUD and instantiation endpoints."""

import json

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import Event, Scenario
from api.routes.admin import require_admin
from builder.scenario import capture_scenario_from_event, instantiate_scenario, scenario_fingerprint

router = APIRouter(prefix="/admin/api", tags=["admin"])


def _scenario_summary(scenario):
    return {"id": scenario.id, "name": scenario.name, "description": scenario.description,
            "version": scenario.version, "created_at": scenario.created_at.isoformat()}


def _validate_name(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("scenario name is required")
    name = value.strip()
    if len(name) > 128:
        raise ValueError("scenario name must be 128 characters or fewer")
    return name


@router.get("/scenarios")
async def list_scenarios(request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    rows = db.query(Scenario).order_by(Scenario.name).all()
    return {"scenarios": [_scenario_summary(s) for s in rows]}


@router.post("/scenarios", status_code=201)
async def create_scenario(request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body = await request.json()
    try:
        name = _validate_name(body.get("name"))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    if db.query(Scenario).filter(Scenario.name == name).first():
        return JSONResponse({"error": "scenario name already exists"}, status_code=409)
    scenario = Scenario(name=name, description=(str(body.get("description") or "").strip() or None),
                        version=1, quota="{}")
    db.add(scenario); db.commit(); db.refresh(scenario)
    return _scenario_summary(scenario)


@router.post("/scenarios/from-event", status_code=201)
async def save_scenario_from_event(request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body = await request.json()
    try:
        name = _validate_name(body.get("name"))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    event = db.query(Event).filter(Event.id == body.get("event_id")).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)

    captured = capture_scenario_from_event(event)
    existing = db.query(Scenario).filter(Scenario.name == name).first()
    if existing:
        scenario = existing
        scenario.version = existing.version + 1
    else:
        scenario = Scenario(name=name, version=1, quota="{}")
        db.add(scenario)
    scenario.description = str(body.get("description") or "").strip() or None
    scenario.quota = json.dumps(captured["quota"])
    scenario.infrastructure = json.dumps(captured["infrastructure"])
    scenario.infrastructure_layout = json.dumps(captured["infrastructure_layout"]) \
        if captured["infrastructure_layout"] is not None else None
    scenario.module_plan = json.dumps(captured["module_plan"])
    scenario.operations_json = json.dumps(captured["operations"])
    scenario.timeline = json.dumps(captured["timeline"])
    scenario.content_fingerprint = scenario_fingerprint(
        captured["quota"], captured["infrastructure"], captured["infrastructure_layout"],
        captured["module_plan"], captured["operations"], captured["timeline"])
    db.commit(); db.refresh(scenario)
    return _scenario_summary(scenario)


@router.get("/scenarios/{scenario_id}")
async def get_scenario(scenario_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    scenario = db.get(Scenario, scenario_id)
    if not scenario:
        return JSONResponse({"error": "Scenario not found"}, status_code=404)
    return {
        **_scenario_summary(scenario),
        "quota": json.loads(scenario.quota) if scenario.quota else {},
        "infrastructure": json.loads(scenario.infrastructure) if scenario.infrastructure else None,
        "module_plan": json.loads(scenario.module_plan) if scenario.module_plan else None,
        "operations": json.loads(scenario.operations_json) if scenario.operations_json else [],
        "timeline": json.loads(scenario.timeline) if scenario.timeline else None,
        "content_fingerprint": scenario.content_fingerprint,
    }


@router.post("/scenarios/{scenario_id}/instantiate")
async def instantiate(scenario_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    scenario = db.get(Scenario, scenario_id)
    if not scenario:
        return JSONResponse({"error": "Scenario not found"}, status_code=404)
    raw = await request.body()
    body = json.loads(raw) if raw else {}
    event_id, report = instantiate_scenario(db, scenario, name=body.get("name"))
    return {"event_id": event_id, "report": report}


@router.delete("/scenarios/{scenario_id}", status_code=204)
async def delete_scenario(scenario_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    scenario = db.get(Scenario, scenario_id)
    if not scenario:
        return JSONResponse({"error": "Scenario not found"}, status_code=404)
    if db.query(Event).filter(Event.scenario_id == scenario_id).first():
        return JSONResponse({"error": "scenario is referenced by an event"}, status_code=409)
    db.delete(scenario); db.commit()
    return Response(status_code=204)


@router.get("/events/{event_id}/timeline")
async def get_timeline(event_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)
    from builder.timeline import empty_timeline, normalize_timeline
    timeline = normalize_timeline(json.loads(event.timeline) if event.timeline else empty_timeline())
    return {"timeline": timeline, "updated_at": event.updated_at.isoformat(),
            "read_only": event.status != "draft"}


@router.put("/events/{event_id}/timeline")
async def save_timeline(event_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)
    if event.status != "draft":
        return JSONResponse({"error": "timeline is read only"}, status_code=409)
    body = await request.json()
    from api.models import utcnow
    from api.routes.admin import _utc_instant
    from builder.timeline import normalize_timeline
    try:
        if _utc_instant(body.get("expected_updated_at")) != _utc_instant(event.updated_at):
            return JSONResponse({"error": "event draft has changed",
                                 "current_updated_at": event.updated_at.isoformat()}, status_code=409)
        timeline = normalize_timeline(body.get("timeline"))
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    event.timeline = json.dumps(timeline); event.updated_at = utcnow(); db.commit(); db.refresh(event)
    from api.services.integration_outbox import enqueue_event_sync
    enqueue_event_sync(event_id, "timeline_updated")
    return {"status": "saved", "updated_at": event.updated_at.isoformat()}


@router.get("/events/{event_id}/plan-health")
async def plan_health(event_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)
    from builder.module_loader import load_all_modules
    from builder.scenario import plan_health as _plan_health
    modules_by_id = {m.id: m for m in load_all_modules()}
    return _plan_health(event, modules_by_id)

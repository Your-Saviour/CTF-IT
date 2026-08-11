import io
import json
import shutil
import uuid
import zipfile

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import Event
from api.routes.admin import require_admin

router = APIRouter(prefix="/admin/api", tags=["admin"])


@router.post("/ansible-export")
async def ansible_export(request: Request, db: Session = Depends(get_db)):
    """Generate an Ansible playbook export as a zip download."""
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    body = await request.json()

    if "event_id" in body:
        event = db.query(Event).filter(Event.id == body["event_id"]).first()
        if not event:
            return JSONResponse({"error": "Event not found"}, status_code=404)
        quota = json.loads(event.quota)
    elif "quota" in body:
        from builder.quota_validation import validate_quota
        errors = validate_quota(body["quota"])
        if errors:
            return JSONResponse(
                {"error": "Invalid quota", "details": errors},
                status_code=422,
            )
        quota = body["quota"]
    else:
        return JSONResponse(
            {"error": "Provide 'quota' or 'event_id'"}, status_code=400
        )

    export_id = f"ansible_{uuid.uuid4().hex[:12]}"

    from builder.ansible import generate_ansible_export
    try:
        output_dir = generate_ansible_export(quota, export_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)

    try:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in output_dir.rglob("*"):
                if file_path.is_file():
                    arcname = file_path.relative_to(output_dir)
                    zf.write(file_path, arcname)
        zip_buffer.seek(0)

        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename={export_id}.zip",
                "Content-Length": str(zip_buffer.getbuffer().nbytes),
            },
        )
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)

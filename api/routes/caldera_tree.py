"""Caldera attack tree API endpoints."""

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import VM, VMModule
from api.routes.admin import require_admin

router = APIRouter(prefix="/admin/caldera", tags=["admin"])


@router.get("/attack-tree/{vm_id}")
async def get_attack_tree(vm_id: int, request: Request, db: Session = Depends(get_db)):
    """Return the attack tree for a VM, computed from its assigned modules.

    If the VM has a cached attack_tree_json, returns that with an is_stale flag.
    Otherwise computes fresh from VMModule assignments.
    """
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        return JSONResponse({"error": "VM not found"}, status_code=404)

    # Get VM's assigned modules
    vm_modules = db.query(VMModule).filter(VMModule.vm_id == vm_id).all()
    if not vm_modules:
        return {"tree": {"nodes": [], "edges": [], "paths": []}, "is_stale": False}

    # Load full module definitions
    from builder.module_loader import load_all_modules
    library = {m.id: m for m in load_all_modules()}

    modules = []
    for vmm in vm_modules:
        mod = library.get(vmm.module_id)
        if mod:
            modules.append(mod)

    # Build fresh tree
    from builder.attack_tree import build_attack_tree, serialize_tree
    tree = build_attack_tree(modules)
    tree_data = serialize_tree(tree)

    # Check if cached version exists and if it's stale
    is_stale = False
    if vm.attack_tree_json:
        try:
            cached = json.loads(vm.attack_tree_json)
            cached_at = cached.get("generated_at", "")
            # Compare with fresh -- if node count or path count differs, it's stale
            if (len(cached.get("nodes", [])) != len(tree_data["nodes"]) or
                    len(cached.get("paths", [])) != len(tree_data["paths"])):
                is_stale = True
        except (json.JSONDecodeError, KeyError):
            is_stale = True

    return {"tree": tree_data, "is_stale": is_stale}

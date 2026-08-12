"""Post-provision training controls and verification baselines."""

import asyncio
import json

from sqlalchemy.orm import Session

from api.models import TeamTrainingCredential, VMModule
from api.services.training_credentials import _deploy, generate_credential
from api.services.verification import capture_baseline
from api.services.verifier_account import provision_verifier
from builder.module_loader import load_all_modules


def finalize_training_vm(db: Session, vm) -> dict:
    assignments = db.query(VMModule).filter(VMModule.vm_id == vm.id, VMModule.stage == "preapplied").all()
    # Goal verification also uses the restricted verifier account, so install
    # it even when this VM has no learner-facing, pre-applied assignments.
    verifier_ok = provision_verifier(vm, db)
    if not assignments:
        return {"verifier": "provisioned" if verifier_ok else "failed",
                "credential": "not_applicable", "baselines": 0}
    credential = vm.team.training_credential
    if credential is None:
        credential = generate_credential(vm.team)
        db.add(credential)
        db.commit()
    credential_ok = _deploy(vm, db, credential)
    if credential_ok:
        provisioned = set(json.loads(credential.provisioned_vm_ids_json or "[]"))
        provisioned.add(vm.id)
        credential.provisioned_vm_ids_json = json.dumps(sorted(provisioned))
        team_active_ids = {candidate.id for candidate in vm.team.vms if candidate.status == "active"}
        credential.status = "active" if team_active_ids <= provisioned else "partial"
    else:
        credential.status = "partial"
        credential.last_error_code = "vm_provision_failed"
    definitions = {module.id: module for module in load_all_modules()}
    captured = 0
    if verifier_ok:
        for assignment in assignments:
            definition = definitions.get(assignment.module_id)
            if not definition:
                assignment.verification_error_code = "module_missing"
                continue
            try:
                baseline = asyncio.run(capture_baseline(vm, definition.verification))
                assignment.verification_baseline_json = json.dumps(baseline, sort_keys=True)
                captured += bool(baseline)
            except Exception:
                assignment.verification_error_code = "baseline_unavailable"
    db.commit()
    return {"verifier": "provisioned" if verifier_ok else "failed",
            "credential": "provisioned" if credential_ok else "failed", "baselines": captured}

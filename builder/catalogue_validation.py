"""Training catalogue contract validation used by readiness and CI."""

from urllib.parse import urlparse

from api.services.verification import InvalidSpecification, validate_spec
from builder.module_loader import Module


def validate_module(module: Module, known_ids: set[str]) -> list[str]:
    errors: list[str] = []
    if module.type in {"vulnerability", "hardening", "payload"} and module.stage == "preapplied":
        if not module.learning_objectives:
            errors.append("missing learning_objectives")
        if not 1 <= module.estimated_minutes <= 480:
            errors.append("estimated_minutes must be between 1 and 480")
        if not isinstance(module.debrief, dict) or not all(
            module.debrief.get(key) for key in ("root_cause", "remediation", "attack_mapping")
        ):
            errors.append("debrief requires root_cause, remediation, and attack_mapping")
    exercise = module.type in {"vulnerability", "hardening", "payload", "goal"}
    if exercise and len(module.references) < 2:
        errors.append("exercise requires at least two authoritative references")
    for reference in module.references:
        if reference.legacy:
            errors.append("reference must use title/url object format")
        if not isinstance(reference.title, str) or not reference.title.strip():
            errors.append("reference title must not be empty")
        if not isinstance(reference.url, str):
            errors.append("reference URL must be a string")
            continue
        parsed = urlparse(reference.url)
        if parsed.scheme != "https" or not parsed.netloc:
            errors.append(f"invalid reference: {reference.url}")
    if "cve" in module.tags:
        hosts = {urlparse(reference.url).hostname or "" for reference in module.references}
        has_vendor = any(host not in {"nvd.nist.gov", "www.cisa.gov", "cisa.gov"} for host in hosts)
        has_government = bool(hosts & {"nvd.nist.gov", "www.cisa.gov", "cisa.gov"})
        if not (has_vendor and has_government):
            errors.append("CVE exercise requires vendor and NVD or CISA references")
    for dependency in [*module.requires, *module.prerequisites, *module.conflicts]:
        if dependency not in known_ids:
            errors.append(f"unknown dependency: {dependency}")
    if module.verification:
        try:
            validate_spec(module.verification)
        except InvalidSpecification as exc:
            errors.append(str(exc))
    if module.stage == "caldera" and module.caldera:
        rendered = str(module.caldera)
        if "example.com" in rendered or "attacker.example" in rendered:
            errors.append("Caldera definition contains a placeholder target")
    return errors


def validate_catalogue(modules: list[Module]) -> dict[str, list[str]]:
    known = {module.id for module in modules}
    return {
        module.id: errors
        for module in modules
        if (errors := validate_module(module, known))
    }

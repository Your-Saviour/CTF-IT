from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class EventPreset:
    id: str
    name: str
    description: str
    modules: list[str]


def load_presets() -> list[EventPreset]:
    root = Path(__file__).resolve().parent.parent / "presets"
    result = []
    for path in sorted(root.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        result.append(EventPreset(data["id"], data["name"], data["description"], data["modules"]))
    return result


def validate_presets(module_ids: set[str], modules=None) -> dict[str, list[str]]:
    by_id = {module.id: module for module in (modules or [])}
    errors = {}
    for preset in load_presets():
        issues = [f"missing module: {item}" for item in sorted(set(preset.modules) - module_ids)]
        selected = [by_id[item] for item in preset.modules if item in by_id]
        if selected:
            if not any(module.type == "payload" or "investigation" in module.tags for module in selected):
                issues.append("preset requires an investigation task")
            if not any(module.verification.get("type") in {"all_of", "any_of"} for module in selected):
                issues.append("preset requires a multi-step remediation")
        if issues:
            errors[preset.id] = issues
    return errors

# builder/fact_contract.py
from __future__ import annotations

import re
from dataclasses import dataclass, field

from builder.module_loader import Module

RECON_MARKER = "VULNERABLE"
GOAL_MARKER = "GOAL_ACHIEVED"
PLATFORM_FACT_TRAITS = {"ctf.hostname", "ctf.ip", "ctf.os", "host.id"}

TRAIT_REF = re.compile(r"#\{([^}]+)\}")


def recon_fact_trait(module_id: str) -> str:
    return f"ctf.vuln.{module_id}"


def goal_fact_trait(goal_id: str) -> str:
    return f"ctf.goal.{goal_id}"


@dataclass
class FactSpec:
    trait: str
    marker: str = ""
    pattern: str = ""
    group: int = 1


@dataclass
class AbilityFacts:
    outputs: list[FactSpec] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)


def _parser_mappings(section: dict) -> list[dict]:
    raw = section.get("parser")
    if not raw:
        return []
    if isinstance(raw, list):
        mappings = []
        for entry in raw:
            mappings.extend(entry.get("mappings", []))
        return mappings
    return raw.get("mappings", []) or [raw]


def _requirement_sources(section: dict) -> list[str]:
    raw = section.get("requirements", [])
    if not raw:
        return []
    sources = []
    for entry in raw:
        if "mappings" in entry:
            sources.extend(m.get("source") for m in entry["mappings"])
        else:
            sources.append(entry.get("source"))
    return [s for s in sources if s]


def _group_int(value) -> int:
    """Coerce a capture-group index to int, falling back to 1 on named/odd values."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 1


def _spec_from_mapping(mapping: dict) -> FactSpec | None:
    trait = mapping.get("source")
    if not trait:
        return None
    vals = mapping.get("custom_parser_vals") or {}
    return FactSpec(
        trait=trait,
        marker=vals.get("marker", ""),
        pattern=vals.get("pattern", ""),
        group=_group_int(vals.get("group", 1)),
    )


def ability_facts(module: Module, phase: str) -> AbilityFacts:
    cal = module.caldera or {}
    section = cal.get(phase, {}) or {}
    command = section.get("command", "")

    outputs: list[FactSpec] = []
    if "outputs" in section:
        outputs = [FactSpec(
            trait=o["trait"],
            marker=o.get("marker", ""),
            pattern=o.get("pattern", ""),
            group=_group_int(o.get("group", 1)),
        ) for o in section["outputs"]]
    elif _parser_mappings(section):
        outputs = [s for m in _parser_mappings(section) if (s := _spec_from_mapping(m))]
    elif phase == "recon" and RECON_MARKER in command:
        outputs = [FactSpec(recon_fact_trait(module.id), marker=RECON_MARKER)]
    elif phase == "exploit" and module.type == "goal" and GOAL_MARKER in command:
        outputs = [FactSpec(goal_fact_trait(module.id), marker=GOAL_MARKER)]

    inputs: list[str] = []
    if "inputs" in section:
        inputs = [i for i in section["inputs"] if isinstance(i, str)]
    elif _requirement_sources(section):
        inputs = _requirement_sources(section)
    elif phase == "exploit" and (cal.get("recon") or {}).get("command"):
        inputs = [recon_fact_trait(module.id)]

    return AbilityFacts(outputs=outputs, inputs=inputs)


def substitute_command(command: str, fact_store: dict[str, str]) -> str:
    return TRAIT_REF.sub(lambda m: fact_store.get(m.group(1), ""), command)


def extract_facts(output: str, specs: list[FactSpec]) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in output.splitlines():
        for spec in specs:
            if spec.marker and spec.marker not in line:
                continue
            if spec.pattern:
                match = re.search(spec.pattern, line)
                if not match:
                    continue
                try:
                    value = match.group(spec.group)
                except IndexError:
                    continue
                found[spec.trait] = value
            else:
                found[spec.trait] = spec.marker or "1"
    return found


def emitted_traits(module: Module) -> set[str]:
    traits: set[str] = set()
    for phase in ("recon", "exploit"):
        for spec in ability_facts(module, phase).outputs:
            traits.add(spec.trait)
    return traits


def validate_module_facts(module: Module) -> list[str]:
    errors: list[str] = []
    for phase in ("recon", "exploit"):
        section = (module.caldera or {}).get(phase) or {}
        for out in section.get("outputs", []):
            trait = out.get("trait", "")
            prefix = f"ctf.{module.id}."
            if not trait.startswith(prefix) and trait != goal_fact_trait(module.id) \
                    and trait != recon_fact_trait(module.id):
                errors.append(f"{phase}.outputs trait '{trait}' must be namespaced '{prefix}...'")
            pattern = out.get("pattern")
            if pattern:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    errors.append(f"{phase}.outputs pattern invalid: {exc}")
    return errors


def validate_catalogue_facts(modules: list[Module]) -> dict[str, list[str]]:
    available = set(PLATFORM_FACT_TRAITS)
    for module in modules:
        available |= emitted_traits(module)
    result: dict[str, list[str]] = {}
    for module in modules:
        errors = validate_module_facts(module)
        for phase in ("recon", "exploit"):
            for trait in ability_facts(module, phase).inputs:
                if trait not in available:
                    errors.append(f"{phase}.inputs references unknown trait '{trait}'")
        if errors:
            result[module.id] = errors
    return result

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

import yaml


@dataclass
class CopyStep:
    src: str   # filename or directory relative to source_dir
    dest: str  # absolute path in container
    mode: Optional[str] = None  # e.g. "0755"


@dataclass
class RunStep:
    script: str  # .sh filename relative to source_dir


Step = Union[CopyStep, RunStep]


def _parse_steps(data: dict) -> list[Step]:
    """Parse steps from YAML data, or convert legacy script field."""
    if "steps" in data:
        steps = []
        for entry in data["steps"]:
            if isinstance(entry, dict) and "copy" in entry:
                cp = entry["copy"]
                steps.append(CopyStep(
                    src=cp["src"], dest=cp["dest"], mode=cp.get("mode"),
                ))
            elif isinstance(entry, dict) and "run" in entry:
                steps.append(RunStep(script=entry["run"]))
            else:
                raise ValueError(f"Unknown step format: {entry}")
        return steps
    elif data.get("script"):
        return [RunStep(script=data["script"])]
    return []


def _default_stage(module_type: str) -> Optional[str]:
    """Return the default stage for a module type.

    vulnerability/payload: "preapplied" (can be overridden to "caldera")
    goal: None (goals don't have a stage — they are always red-team objectives)
    all others: None
    """
    if module_type in ("vulnerability", "payload"):
        return "preapplied"
    return None


@dataclass
class Module:
    id: str
    name: str
    description: str
    type: str  # "vulnerability", "hardening", "payload", "application_external", "application_internal", or "goal"
    difficulty: str  # "easy", "medium", "hard"
    points: int
    category: str
    tags: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    script: Optional[str] = None
    steps: list[Step] = field(default_factory=list)
    verification: dict = field(default_factory=dict)
    hints: list[str] = field(default_factory=list)
    suggested_fix: Optional[str] = None
    caldera: Optional[dict] = None
    source_dir: Path = field(default_factory=Path)
    disabled: bool = False
    min_ram_mb: int = 0
    min_vcpu: int = 0
    supported_bases: list[str] = field(default_factory=list)
    # Stage: "preapplied" (blue team sees + fixes) or "caldera" (red team exploits).
    # Defaults via _default_stage(); None for types where stage doesn't apply.
    # When stage is None, __post_init__ fills in the type-based default.
    stage: Optional[str] = None
    # Goal-specific scoring fields (only meaningful when type == "goal")
    red_points: int = 0
    defend_points: int = 0
    revert_verification: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.stage is None:
            self.stage = _default_stage(self.type)


MODULES_DIR = Path(__file__).resolve().parent.parent / "modules"


def load_all_modules() -> list[Module]:
    modules = []
    for yaml_path in sorted(MODULES_DIR.rglob("*.yaml")):
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        module_type = data["type"]
        modules.append(Module(
            id=data["id"],
            name=data["name"],
            description=data["description"],
            type=module_type,
            difficulty=data["difficulty"],
            points=data["points"],
            category=data["category"],
            tags=data.get("tags", []),
            conflicts=data.get("conflicts", []),
            requires=data.get("requires", []),
            script=data.get("script"),
            steps=_parse_steps(data),
            verification=data.get("verification", {}),
            hints=data.get("hints", []),
            suggested_fix=data.get("suggested_fix"),
            caldera=data.get("caldera"),
            source_dir=yaml_path.parent,
            disabled=bool(data.get("disabled", False)),
            min_ram_mb=data.get("min_ram_mb", 0),
            min_vcpu=data.get("min_vcpu", 0),
            supported_bases=data.get("supported_bases", []),
            stage=data.get("stage"),
            red_points=data.get("red_points", 0),
            defend_points=data.get("defend_points", 0),
            revert_verification=data.get("revert_verification", {}),
        ))
    return modules

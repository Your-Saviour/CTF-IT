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


@dataclass
class Module:
    id: str
    name: str
    description: str
    type: str  # "vulnerability", "hardening", or "application"
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
    source_dir: Path = field(default_factory=Path)


MODULES_DIR = Path(__file__).resolve().parent.parent / "modules"


def load_all_modules() -> list[Module]:
    modules = []
    for yaml_path in sorted(MODULES_DIR.rglob("*.yaml")):
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        modules.append(Module(
            id=data["id"],
            name=data["name"],
            description=data["description"],
            type=data["type"],
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
            source_dir=yaml_path.parent,
        ))
    return modules

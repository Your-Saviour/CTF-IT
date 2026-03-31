from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class Module:
    id: str
    name: str
    description: str
    type: str  # "vulnerability" or "hardening"
    difficulty: str  # "easy", "medium", "hard"
    points: int
    category: str
    tags: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    script: Optional[str] = None
    verification: dict = field(default_factory=dict)
    hints: list[str] = field(default_factory=list)
    suggested_fix: Optional[str] = None


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
            verification=data.get("verification", {}),
            hints=data.get("hints", []),
            suggested_fix=data.get("suggested_fix"),
        ))
    return modules

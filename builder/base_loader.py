from dataclasses import dataclass, field
from pathlib import Path
from typing import Union

import yaml

from builder.module_loader import CopyStep, RunStep

Step = Union[CopyStep, RunStep]


@dataclass
class PlaybookStep:
    playbook: str  # .yml filename relative to source_dir


BaseStep = Union[CopyStep, RunStep, PlaybookStep]


def _parse_base_steps(data: dict, source_dir: Path) -> list[BaseStep]:
    """Parse steps from YAML data, handling run:, copy:, and playbook: entries."""
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
            elif isinstance(entry, dict) and "playbook" in entry:
                steps.append(PlaybookStep(playbook=entry["playbook"]))
            else:
                raise ValueError(f"Unknown step format: {entry}")
        return steps
    elif data.get("script"):
        return [RunStep(script=data["script"])]
    return []


@dataclass
class BaseType:
    id: str
    name: str
    description: str
    os: str
    default_plan: str
    packages: list[str] = field(default_factory=list)
    steps: list[BaseStep] = field(default_factory=list)
    disabled: bool = False
    source_dir: Path = field(default_factory=Path)


BASES_DIR = Path(__file__).resolve().parent.parent / "bases"


def load_base_type(base_id: str) -> BaseType:
    """Load a single base type by ID from bases/<base_id>/<base_id>.yaml."""
    yaml_path = BASES_DIR / base_id / f"{base_id}.yaml"
    with open(yaml_path) as f:
        data = yaml.safe_load(f)
    source_dir = yaml_path.parent
    return BaseType(
        id=data["id"],
        name=data["name"],
        description=data["description"],
        os=data["os"],
        default_plan=data["default_plan"],
        packages=data.get("packages", []),
        steps=_parse_base_steps(data, source_dir),
        disabled=bool(data.get("disabled", False)),
        source_dir=source_dir,
    )


def load_all_bases() -> list[BaseType]:
    """Scan bases/ directory and load all base types (including disabled)."""
    bases = []
    if not BASES_DIR.is_dir():
        return bases
    for base_dir in sorted(BASES_DIR.iterdir()):
        if not base_dir.is_dir():
            continue
        yaml_path = base_dir / f"{base_dir.name}.yaml"
        if not yaml_path.exists():
            continue
        bases.append(load_base_type(base_dir.name))
    return bases

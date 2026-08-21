import os
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


@dataclass(frozen=True)
class DeploymentFactSpec:
    trait: str
    label: str
    value_type: str = "string"
    secret: bool = False
    consume_as: Optional[str] = None


@dataclass(frozen=True)
class DeploymentContract:
    repository: str
    branch: str
    inputs: tuple[DeploymentFactSpec, ...] = ()
    outputs: tuple[DeploymentFactSpec, ...] = ()
    completion_check: dict = field(default_factory=dict)


def _parse_deployment(value: object) -> Optional[DeploymentContract]:
    if value is None:
        return None
    if isinstance(value, DeploymentContract):
        return value
    if not isinstance(value, dict):
        raise ValueError("deployment must be an object")
    def facts(name: str) -> tuple[DeploymentFactSpec, ...]:
        result = tuple(DeploymentFactSpec(
            trait=item["trait"], label=item["label"], value_type=item.get("value_type", "string"),
            secret=bool(item.get("secret", False)), consume_as=item.get("consume_as"),
        ) for item in value.get(name, []))
        traits = [item.trait for item in result]
        if len(traits) != len(set(traits)) or any(not trait or " " in trait for trait in traits):
            raise ValueError(f"deployment.{name} contains invalid or duplicate traits")
        return result
    return DeploymentContract(
        repository=value.get("repository", ""), branch=value.get("branch", ""),
        inputs=facts("inputs"), outputs=facts("outputs"),
        completion_check=value.get("completion_check", {}),
    )


@dataclass(frozen=True)
class Reference:
    title: str
    url: str
    legacy: bool = field(default=False, compare=False, repr=False)

    def as_dict(self) -> dict[str, str]:
        return {"title": self.title, "url": self.url}


def _parse_references(entries: object) -> list[Reference]:
    """Normalize legacy URL strings while retaining malformed entries for validation."""
    if not isinstance(entries, list):
        return [Reference(title="", url="")]
    references = []
    for entry in entries:
        if isinstance(entry, str):
            references.append(Reference(title=entry, url=entry, legacy=True))
        elif isinstance(entry, dict):
            references.append(Reference(title=entry.get("title", ""), url=entry.get("url", "")))
        else:
            references.append(Reference(title="", url=""))
    return references


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
    learning_objectives: list[str] = field(default_factory=list)
    estimated_minutes: int = 0
    prerequisites: list[str] = field(default_factory=list)
    phases: list[str] = field(default_factory=list)
    narrative: str = ""
    references: list[Reference] = field(default_factory=list)
    debrief: dict = field(default_factory=dict)
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
    deployment: Optional[DeploymentContract] = None

    def __post_init__(self):
        self.deployment = _parse_deployment(self.deployment)
        if self.type == "green_infrastructure" and not self.deployment:
            raise ValueError("green_infrastructure modules require a deployment contract")
        if self.stage is None:
            self.stage = _default_stage(self.type)
        if self.type in {"vulnerability", "hardening", "payload"}:
            if not self.learning_objectives:
                self.learning_objectives = [f"Identify the root cause of {self.name.lower()}", "Apply and validate a durable remediation"]
            if not self.estimated_minutes:
                self.estimated_minutes = {"easy": 20, "medium": 40, "hard": 75}.get(self.difficulty, 40)
            if not self.prerequisites:
                self.prerequisites = list(self.requires)
            if not self.debrief:
                technique = ((self.caldera or {}).get("technique") or {})
                category_mappings = {
                    "authentication": "T1078 — Valid Accounts",
                    "filesystem": "T1222.002 — Linux and Mac File and Directory Permissions Modification",
                    "network": "T1049 — System Network Connections Discovery",
                    "persistence": "T1546 — Event Triggered Execution",
                    "web": "T1190 — Exploit Public-Facing Application",
                    "containers": "T1611 — Escape to Host",
                    "logging": "T1562.002 — Impair Defenses",
                    "incident-response": "T1087 — Account Discovery",
                    "compliance": "T1548.001 — Setuid and Setgid",
                }
                mapping = " — ".join(filter(None, (technique.get("attack_id"), technique.get("name")))) or category_mappings.get(self.category, "T1082 — System Information Discovery")
                self.debrief = {
                    "root_cause": self.description,
                    "remediation": self.suggested_fix or "Remove the unsafe condition and verify the affected service still works.",
                    "attack_mapping": mapping,
                }


MODULES_DIR = Path(__file__).resolve().parent.parent / "modules"
MODULE_REPOS_DIR = Path(os.environ.get("MODULE_REPOS_DIR", "/app/module_repos"))


def module_from_yaml(yaml_path: Path) -> Module:
    with open(yaml_path) as f:
        data = yaml.safe_load(f)
    module_type = data["type"]
    return Module(
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
        learning_objectives=data.get("learning_objectives", []),
        estimated_minutes=data.get("estimated_minutes", 0),
        prerequisites=data.get("prerequisites", data.get("requires", [])),
        phases=data.get("phases", []),
        narrative=data.get("narrative", ""),
        references=_parse_references(data.get("references", [])),
        debrief=data.get("debrief", {}),
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
        deployment=data.get("deployment"),
    )


def _module_roots() -> list[Path]:
    roots = [MODULES_DIR]
    if MODULE_REPOS_DIR.is_dir():
        roots.extend(sorted(
            p for p in MODULE_REPOS_DIR.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        ))
    return roots


def load_all_modules() -> list[Module]:
    modules = []
    for root in _module_roots():
        for yaml_path in sorted(root.rglob("*.yaml")):
            if ".git" in yaml_path.parts:
                continue
            modules.append(module_from_yaml(yaml_path))
    # Dependent remediation must preserve its application foundation. Compose
    # that health contract automatically so catalogue authors cannot silently
    # ship a file-only check that rewards breaking the service.
    by_id = {module.id: module for module in modules}
    for module in modules:
        foundations = [by_id[item] for item in module.requires
                       if item in by_id and by_id[item].type in {"application_external", "application_internal"}]
        if module.type in {"vulnerability", "hardening", "payload"} and foundations and module.verification:
            health_checks = [foundation.verification for foundation in foundations if foundation.verification]
            if health_checks:
                module.verification = {"type": "all_of", "checks": [module.verification, *health_checks]}
    return modules

import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from builder.base_loader import BaseType, PlaybookStep
from builder.module_loader import CopyStep, RunStep

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"


def _build_base_ops(base_type: BaseType) -> list[dict]:
    """Convert a BaseType into an operations list for the base playbook template."""
    ops = []

    if base_type.packages:
        ops.append({
            "type": "package_install",
            "packages": base_type.packages,
        })

    for step in base_type.steps:
        if isinstance(step, RunStep):
            ops.append({
                "type": "run",
                "script": f"{base_type.id}__{step.script}",
            })
        elif isinstance(step, CopyStep):
            ops.append({
                "type": "copy",
                "src": f"{base_type.id}__{step.src}",
                "dest": step.dest,
                "mode": step.mode,
            })
        elif isinstance(step, PlaybookStep):
            ops.append({
                "type": "playbook",
                "playbook": f"{base_type.id}__{step.playbook}",
            })

    return ops


def render_base_playbook(base_type: BaseType) -> str:
    """Render base_playbook.yml from the Jinja2 template for the given base type."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("base_playbook.yml.j2")
    ops = _build_base_ops(base_type)
    return template.render(base_type=base_type, ops=ops)


def stage_base_files(base_type: BaseType, export_dir: Path) -> None:
    """Copy base type scripts, files, and playbook task files into the staging directory."""
    scripts_dir = export_dir / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    files_dir = export_dir / "files"
    files_dir.mkdir(exist_ok=True)

    for step in base_type.steps:
        if isinstance(step, RunStep):
            src = base_type.source_dir / step.script
            shutil.copy2(src, scripts_dir / f"{base_type.id}__{step.script}")
        elif isinstance(step, CopyStep):
            src = base_type.source_dir / step.src
            staged_name = f"{base_type.id}__{step.src}"
            if src.is_dir():
                shutil.copytree(src, files_dir / staged_name)
            else:
                shutil.copy2(src, files_dir / staged_name)
        elif isinstance(step, PlaybookStep):
            src = base_type.source_dir / step.playbook
            shutil.copy2(src, files_dir / f"{base_type.id}__{step.playbook}")

import json
import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from builder.module_loader import CopyStep, Module, RunStep

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"
MODULES_DIR = PROJECT_ROOT / "modules"
BUILD_CONTEXTS_DIR = PROJECT_ROOT / "build_contexts"
AUDIT_SCRIPT = PROJECT_ROOT / "audit.py"
BUILD_SNAPSHOT_SCRIPT = Path(__file__).resolve().parent / "build_snapshot.py"


def render_dockerfile(modules: list[Module]) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("Dockerfile.j2")
    operations = []
    for m in modules:
        for step in m.steps:
            if isinstance(step, RunStep):
                operations.append({
                    "type": "run",
                    "script": f"{m.id}__{step.script}",
                })
            elif isinstance(step, CopyStep):
                operations.append({
                    "type": "copy",
                    "staged": f"{m.id}__{step.src}",
                    "dest": step.dest,
                    "mode": step.mode,
                })
    return template.render(operations=operations)


def generate_state_file(user_id: str) -> dict:
    """Minimal opaque state file — no module info."""
    return {
        "user_id": user_id,
        "snapshots": {},
    }


def prepare_build_context(
    user_id: str, modules: list[Module], flag: str, image_tag: str
) -> Path:
    context_dir = BUILD_CONTEXTS_DIR / image_tag
    context_dir.mkdir(parents=True, exist_ok=True)

    # Write Dockerfile
    dockerfile_content = render_dockerfile(modules)
    (context_dir / "Dockerfile").write_text(dockerfile_content)

    # Stage module scripts and files
    scripts_dir = context_dir / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    files_dir = context_dir / "files"
    files_dir.mkdir(exist_ok=True)
    for m in modules:
        for step in m.steps:
            if isinstance(step, RunStep):
                src = m.source_dir / step.script
                shutil.copy2(src, scripts_dir / f"{m.id}__{step.script}")
            elif isinstance(step, CopyStep):
                src = m.source_dir / step.src
                staged_name = f"{m.id}__{step.src}"
                if src.is_dir():
                    shutil.copytree(src, files_dir / staged_name)
                else:
                    shutil.copy2(src, files_dir / staged_name)

    # Write opaque state file (no module info)
    state = generate_state_file(user_id)
    (context_dir / "state.json").write_text(json.dumps(state, indent=2))

    # Copy audit script
    if AUDIT_SCRIPT.exists():
        shutil.copy2(AUDIT_SCRIPT, context_dir / "audit.py")

    # Copy build_snapshot.py for build-time state capture
    if BUILD_SNAPSHOT_SCRIPT.exists():
        shutil.copy2(BUILD_SNAPSHOT_SCRIPT, context_dir / "build_snapshot.py")

    return context_dir

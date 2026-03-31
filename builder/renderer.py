import json
import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from builder.module_loader import Module

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"
MODULES_DIR = PROJECT_ROOT / "modules"
BUILD_CONTEXTS_DIR = PROJECT_ROOT / "build_contexts"
COLLECT_SCRIPT = PROJECT_ROOT / "collect.py"
BUILD_SNAPSHOT_SCRIPT = Path(__file__).resolve().parent / "build_snapshot.py"


def render_dockerfile(modules: list[Module]) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("Dockerfile.j2")
    vuln_scripts = [m.script for m in modules if m.script]
    return template.render(vuln_scripts=vuln_scripts)


def generate_manifest(user_id: str, modules: list[Module]) -> dict:
    return {
        "user_id": user_id,
        "modules": [
            {
                "id": m.id,
                "name": m.name,
                "type": m.type,
                "difficulty": m.difficulty,
                "points": m.points,
                "verification": m.verification,
            }
            for m in modules
        ],
    }


def prepare_build_context(
    user_id: str, modules: list[Module], flag: str, image_tag: str
) -> Path:
    context_dir = BUILD_CONTEXTS_DIR / image_tag
    context_dir.mkdir(parents=True, exist_ok=True)

    # Write Dockerfile
    dockerfile_content = render_dockerfile(modules)
    (context_dir / "Dockerfile").write_text(dockerfile_content)

    # Copy vuln scripts
    scripts_dir = context_dir / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    for m in modules:
        if m.script:
            src = MODULES_DIR / "vulns" / m.id / m.script
            shutil.copy2(src, scripts_dir / m.script)

    # Write manifest
    manifest = generate_manifest(user_id, modules)
    (context_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    # Copy collect.py
    if COLLECT_SCRIPT.exists():
        shutil.copy2(COLLECT_SCRIPT, context_dir / "collect.py")

    # Copy build_snapshot.py for build-time manifest enrichment
    if BUILD_SNAPSHOT_SCRIPT.exists():
        shutil.copy2(BUILD_SNAPSHOT_SCRIPT, context_dir / "build_snapshot.py")

    return context_dir

import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from builder.module_loader import CopyStep, Module, RunStep, load_all_modules
from builder.selector import select_modules

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"
ANSIBLE_EXPORTS_DIR = PROJECT_ROOT / "ansible_exports"


def _build_operations(modules: list[Module]) -> list[dict]:
    """Convert module steps to an operations list for the playbook template."""
    operations = []
    for m in modules:
        for step in m.steps:
            if isinstance(step, RunStep):
                operations.append({
                    "type": "run",
                    "script": f"{m.id}__{step.script}",
                    "module_name": m.name,
                })
            elif isinstance(step, CopyStep):
                operations.append({
                    "type": "copy",
                    "staged": f"{m.id}__{step.src}",
                    "dest": step.dest,
                    "mode": step.mode,
                    "module_name": m.name,
                })
    return operations


def render_playbook(modules: list[Module]) -> str:
    """Render playbook.yml from the Jinja2 template."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("playbook.yml.j2")
    operations = _build_operations(modules)
    module_names = [m.name for m in modules]
    return template.render(operations=operations, module_names=module_names)


def _stage_files(modules: list[Module], output_dir: Path) -> None:
    """Copy module scripts and files into the export directory."""
    scripts_dir = output_dir / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    files_dir = output_dir / "files"
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


def generate_ansible_export(quota: dict, export_id: str) -> Path:
    """Select modules via quota and generate an Ansible playbook export directory."""
    library = load_all_modules()
    selected = select_modules(quota, library)

    output_dir = ANSIBLE_EXPORTS_DIR / export_id
    output_dir.mkdir(parents=True, exist_ok=True)

    playbook_content = render_playbook(selected)
    (output_dir / "playbook.yml").write_text(playbook_content)

    _stage_files(selected, output_dir)

    return output_dir

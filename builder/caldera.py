import shutil
import uuid
from collections import defaultdict
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from builder.module_loader import Module, load_all_modules
from builder.selector import select_modules

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"
CALDERA_EXPORTS_DIR = PROJECT_ROOT / "caldera_exports"

# Namespace UUID for deterministic ability ID generation
_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

PLUGIN_NAME = "ctf-exploit"


def _ability_uuid(module_id: str, phase: str) -> str:
    """Generate a deterministic UUID for a module ability."""
    return str(uuid.uuid5(_NAMESPACE, f"{module_id}_{phase}"))


def _adversary_uuid(name: str) -> str:
    """Generate a deterministic UUID for an adversary profile."""
    return str(uuid.uuid5(_NAMESPACE, f"adversary_{name}"))


def _build_abilities(modules: list[Module]) -> list[dict]:
    """Build ability dicts for all vulnerability modules with caldera metadata."""
    abilities = []
    for m in modules:
        if m.type != "vulnerability" or not m.caldera:
            continue

        cal = m.caldera
        tactic = cal["tactic"]
        technique = cal["technique"]

        # Recon ability
        recon = cal.get("recon", {})
        if recon.get("command"):
            abilities.append({
                "id": _ability_uuid(m.id, "recon"),
                "module_id": m.id,
                "name": f"Recon: {m.name}",
                "description": recon.get("description", f"Reconnaissance for {m.name}"),
                "tactic": tactic,
                "technique_attack_id": technique["attack_id"],
                "technique_name": technique["name"],
                "command": recon["command"].strip(),
                "cleanup": recon.get("cleanup", "").strip(),
                "payloads": cal.get("payloads", []),
                "phase": "recon",
            })

        # Exploit ability
        exploit = cal.get("exploit", {})
        if exploit.get("command"):
            abilities.append({
                "id": _ability_uuid(m.id, "exploit"),
                "module_id": m.id,
                "name": f"Exploit: {m.name}",
                "description": exploit.get("description", f"Exploit {m.name}"),
                "tactic": tactic,
                "technique_attack_id": technique["attack_id"],
                "technique_name": technique["name"],
                "command": exploit["command"].strip(),
                "cleanup": exploit.get("cleanup", "").strip(),
                "payloads": cal.get("payloads", []),
                "phase": "exploit",
            })

    return abilities


def _render_ability(env: Environment, ability: dict) -> str:
    """Render a single ability YAML file."""
    template = env.get_template("caldera_ability.yml.j2")
    return template.render(
        ability_id=ability["id"],
        ability_name=ability["name"],
        ability_description=ability["description"],
        tactic=ability["tactic"],
        technique_attack_id=ability["technique_attack_id"],
        technique_name=ability["technique_name"],
        command=ability["command"],
        cleanup=ability["cleanup"],
        payloads=ability["payloads"],
    )


def _build_adversary_profiles(abilities: list[dict]) -> list[dict]:
    """Build master and per-tactic adversary profiles."""
    profiles = []

    # Master profile: all recon first, then all exploit
    recon_abilities = [a for a in abilities if a["phase"] == "recon"]
    exploit_abilities = [a for a in abilities if a["phase"] == "exploit"]
    ordered = recon_abilities + exploit_abilities

    profiles.append({
        "id": _adversary_uuid("master"),
        "name": "CTF Full Exploit Chain",
        "description": "All CTF vulnerability recon and exploitation abilities",
        "abilities": [
            {"id": a["id"], "comment": a["name"]}
            for a in ordered
        ],
    })

    # Per-tactic profiles
    by_tactic = defaultdict(list)
    for a in abilities:
        by_tactic[a["tactic"]].append(a)

    for tactic, tactic_abilities in sorted(by_tactic.items()):
        recon = [a for a in tactic_abilities if a["phase"] == "recon"]
        exploit = [a for a in tactic_abilities if a["phase"] == "exploit"]
        ordered = recon + exploit

        profiles.append({
            "id": _adversary_uuid(tactic),
            "name": f"CTF {tactic.replace('-', ' ').title()}",
            "description": f"CTF {tactic} abilities — recon then exploit",
            "abilities": [
                {"id": a["id"], "comment": a["name"]}
                for a in ordered
            ],
        })

    return profiles


def generate_caldera_export(quota: dict, export_id: str) -> Path:
    """Select modules via quota and generate a Caldera plugin export directory."""
    library = load_all_modules()
    selected = select_modules(quota, library)

    abilities = _build_abilities(selected)
    if not abilities:
        raise ValueError("No vulnerability modules with caldera metadata were selected")

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))

    # Set up plugin directory structure
    output_dir = CALDERA_EXPORTS_DIR / export_id
    plugin_dir = output_dir / "plugins" / PLUGIN_NAME
    abilities_dir = plugin_dir / "data" / "abilities"
    adversaries_dir = plugin_dir / "data" / "adversaries"
    payloads_dir = plugin_dir / "payloads"

    for d in [abilities_dir, adversaries_dir, payloads_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # Write hook.py
    hook_template = env.get_template("caldera_hook.py.j2")
    (plugin_dir / "hook.py").write_text(hook_template.render())

    # Write ability files organized by tactic
    for ability in abilities:
        tactic_dir = abilities_dir / ability["tactic"]
        tactic_dir.mkdir(exist_ok=True)

        content = _render_ability(env, ability)
        filename = f"{ability['id']}.yml"
        (tactic_dir / filename).write_text(content)

    # Write adversary profiles
    profiles = _build_adversary_profiles(abilities)
    adversary_template = env.get_template("caldera_adversary.yml.j2")
    for profile in profiles:
        content = adversary_template.render(
            adversary_id=profile["id"],
            adversary_name=profile["name"],
            adversary_description=profile["description"],
            abilities=profile["abilities"],
        )
        (adversaries_dir / f"{profile['id']}.yml").write_text(content)

    # Copy payload files if any modules reference them
    for m in selected:
        if m.caldera and m.caldera.get("payloads"):
            for payload in m.caldera["payloads"]:
                src = m.source_dir / payload
                if src.exists():
                    shutil.copy2(src, payloads_dir / f"{m.id}__{payload}")

    return output_dir

import shutil
import uuid
from collections import defaultdict
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from builder.attack_tree import build_attack_tree, AttackTree, TACTIC_PHASE
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


def ability_uuid(module_id: str, phase: str) -> str:
    """Public alias for deterministic ability UUID generation.

    Returns the same UUID that generate_caldera_export() uses for abilities,
    enabling reverse-lookup of which CTF module an operation result belongs to.
    """
    return _ability_uuid(module_id, phase)


def build_ability_uuid_map(modules: list) -> dict[str, dict]:
    """Return a mapping of ability_uuid -> {module_id, module_name, phase}.

    Used by the operations results endpoint to annotate Caldera link results
    with the corresponding CTF module name.
    """
    result = {}
    for m in modules:
        if not m.caldera or m.type.startswith("application_"):
            continue
        cal = m.caldera
        if cal.get("recon", {}).get("command"):
            result[_ability_uuid(m.id, "recon")] = {
                "module_id": m.id,
                "module_name": m.name,
                "phase": "recon",
            }
        if cal.get("exploit", {}).get("command"):
            result[_ability_uuid(m.id, "exploit")] = {
                "module_id": m.id,
                "module_name": m.name,
                "phase": "exploit",
            }
    return result


def _build_abilities(modules: list[Module]) -> list[dict]:
    """Build ability dicts for all modules with caldera metadata (excluding applications)."""
    abilities = []
    for m in modules:
        if not m.caldera or m.type.startswith("application_"):
            continue

        cal = m.caldera
        tactic = cal["tactic"]
        technique = cal["technique"]

        # Recon ability
        recon = cal.get("recon", {})
        if recon.get("command"):
            recon_payloads = recon.get("payloads", [])
            prefixed_recon_payloads = [f"{m.id}__{p}" for p in recon_payloads]
            command = recon["command"].strip()
            for orig, prefixed in zip(recon_payloads, prefixed_recon_payloads):
                command = command.replace(f"{{{{ payload.{orig} }}}}", f"{{{{ payload.{prefixed} }}}}")
            abilities.append({
                "id": _ability_uuid(m.id, "recon"),
                "module_id": m.id,
                "name": f"Recon: {m.name}",
                "description": recon.get("description", f"Reconnaissance for {m.name}"),
                "tactic": tactic,
                "technique_attack_id": technique["attack_id"],
                "technique_name": technique["name"],
                "command": command,
                "cleanup": recon.get("cleanup", "").strip(),
                "payloads": prefixed_recon_payloads,
                "phase": "recon",
            })

        # Exploit ability
        exploit = cal.get("exploit", {})
        if exploit.get("command"):
            exploit_payloads = exploit.get("payloads", [])
            prefixed_exploit_payloads = [f"{m.id}__{p}" for p in exploit_payloads]
            command = exploit["command"].strip()
            for orig, prefixed in zip(exploit_payloads, prefixed_exploit_payloads):
                command = command.replace(f"{{{{ payload.{orig} }}}}", f"{{{{ payload.{prefixed} }}}}")
            abilities.append({
                "id": _ability_uuid(m.id, "exploit"),
                "module_id": m.id,
                "name": f"Exploit: {m.name}",
                "description": exploit.get("description", f"Exploit {m.name}"),
                "tactic": tactic,
                "technique_attack_id": technique["attack_id"],
                "technique_name": technique["name"],
                "command": command,
                "cleanup": exploit.get("cleanup", "").strip(),
                "payloads": prefixed_exploit_payloads,
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


def _wrap_command_with_skip_logic(command: str, phase_num: int, ability_phase: str) -> str:
    """Wrap an ability command with skip-logic preamble for multi-path adversaries.

    For recon abilities: skip if this kill chain phase is already completed,
    and mark phase as completed on success.
    For exploit abilities: skip if recon for this phase failed.
    """
    if ability_phase == "recon":
        return (
            f"# Skip if this kill chain phase already completed\n"
            f"if [ -f /tmp/.ctf_phase_{phase_num} ]; then "
            f"echo \"SKIPPED: phase already completed\"; exit 0; fi\n"
            f"{command}\n"
            f"# Mark phase as completed on success\n"
            f"echo \"PHASE_{phase_num}_SUCCESS\" > /tmp/.ctf_phase_{phase_num}"
        )
    else:  # exploit
        return (
            f"# Skip if recon for this phase failed\n"
            f"if [ ! -f /tmp/.ctf_phase_{phase_num} ]; then "
            f"echo \"SKIPPED: recon failed\"; exit 0; fi\n"
            f"{command}"
        )


def build_path_adversaries(
    tree: AttackTree, abilities: list[dict], vm_hostname: str = ""
) -> list[dict]:
    """Build per-path adversary profiles from an AttackTree.

    Each path in the tree becomes an adversary that chains the recon and exploit
    abilities for each module in the path.
    """
    # Build lookup: module_id -> list of abilities (recon first, exploit second)
    abilities_by_module: dict[str, dict[str, dict]] = defaultdict(dict)
    for a in abilities:
        abilities_by_module[a["module_id"]][a["phase"]] = a

    profiles = []
    for i, path in enumerate(tree.paths):
        if not path:
            continue

        # Collect module names for naming
        module_names = []
        for mid in path:
            node = tree.nodes.get(mid)
            if node:
                module_names.append(node.module_name)

        first_name = module_names[0] if module_names else "Unknown"
        last_name = module_names[-1] if module_names else "Unknown"

        hostname_part = f" {vm_hostname}" if vm_hostname else ""
        adv_name = f"CTF{hostname_part} Path {i + 1}: {first_name} -> {last_name}"

        # Build atomic ordering: recon then exploit for each module in path order
        atomic_ordering = []
        for mid in path:
            mod_abilities = abilities_by_module.get(mid, {})
            if "recon" in mod_abilities:
                atomic_ordering.append({
                    "id": mod_abilities["recon"]["id"],
                    "comment": mod_abilities["recon"]["name"],
                })
            if "exploit" in mod_abilities:
                atomic_ordering.append({
                    "id": mod_abilities["exploit"]["id"],
                    "comment": mod_abilities["exploit"]["name"],
                })

        if not atomic_ordering:
            continue

        profiles.append({
            "id": _adversary_uuid(f"path_{vm_hostname}_{i}"),
            "name": adv_name,
            "description": f"Attack path: {' -> '.join(module_names)}",
            "abilities": atomic_ordering,
        })

    return profiles


def generate_caldera_export_multi_path(
    modules: list[Module], export_id: str, vm_hostname: str = ""
) -> tuple[Path, AttackTree]:
    """Generate a Caldera plugin export with per-path adversary profiles.

    Takes pre-selected modules (not a quota) and generates both legacy
    adversary profiles and per-path adversaries based on the attack tree.

    Returns (output_dir, tree) so callers can store the tree.
    """
    abilities = _build_abilities(modules)
    if not abilities:
        raise ValueError("No modules with caldera metadata were selected")

    tree = build_attack_tree(modules)

    # Build wrapped abilities for multi-path mode
    # Build a lookup from module_id to its tactic phase number
    module_phase: dict[str, int] = {}
    for a in abilities:
        if a["module_id"] not in module_phase:
            cal = None
            for m in modules:
                if m.id == a["module_id"] and m.caldera:
                    cal = m.caldera
                    break
            if cal:
                tactic = cal["tactic"]
                phase_override = cal.get("phase_override")
                phase_num = phase_override if phase_override is not None else TACTIC_PHASE.get(tactic, 0)
                module_phase[a["module_id"]] = phase_num

    wrapped_abilities = []
    for a in abilities:
        phase_num = module_phase.get(a["module_id"], 0)
        wrapped = dict(a)
        wrapped["command"] = _wrap_command_with_skip_logic(
            a["command"], phase_num, a["phase"]
        )
        wrapped_abilities.append(wrapped)

    # Generate path adversaries from wrapped abilities
    path_adversaries = build_path_adversaries(tree, wrapped_abilities, vm_hostname)

    # Generate legacy master + per-tactic adversaries (from unwrapped abilities)
    legacy_adversaries = _build_adversary_profiles(abilities)

    all_adversaries = legacy_adversaries + path_adversaries

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

    # Write ability files (use wrapped abilities so skip logic is baked in)
    for ability in wrapped_abilities:
        tactic_dir = abilities_dir / ability["tactic"]
        tactic_dir.mkdir(exist_ok=True)

        content = _render_ability(env, ability)
        filename = f"{ability['id']}.yml"
        (tactic_dir / filename).write_text(content)

    # Write adversary profiles
    adversary_template = env.get_template("caldera_adversary.yml.j2")
    for profile in all_adversaries:
        content = adversary_template.render(
            adversary_id=profile["id"],
            adversary_name=profile["name"],
            adversary_description=profile["description"],
            abilities=profile["abilities"],
        )
        (adversaries_dir / f"{profile['id']}.yml").write_text(content)

    # Copy payload files
    for m in modules:
        if not m.caldera:
            continue
        for section in ["recon", "exploit"]:
            section_data = m.caldera.get(section, {})
            for payload in section_data.get("payloads", []):
                src = m.source_dir / payload
                if src.exists():
                    shutil.copy2(src, payloads_dir / f"{m.id}__{payload}")

    return output_dir, tree


def generate_caldera_export(quota: dict, export_id: str) -> Path:
    """Select modules via quota and generate a Caldera plugin export directory."""
    library = load_all_modules()
    selected = select_modules(quota, library, base_type_id=None)

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
        if not m.caldera:
            continue
        for section in ["recon", "exploit"]:
            section_data = m.caldera.get(section, {})
            for payload in section_data.get("payloads", []):
                src = m.source_dir / payload
                if src.exists():
                    shutil.copy2(src, payloads_dir / f"{m.id}__{payload}")

    return output_dir

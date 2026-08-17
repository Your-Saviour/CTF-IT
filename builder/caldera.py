import shutil
import uuid
from collections import defaultdict
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from builder.attack_tree import build_attack_tree, AttackTree
from builder.module_loader import Module, load_all_modules
from builder.selector import select_modules

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"
CALDERA_EXPORTS_DIR = PROJECT_ROOT / "caldera_exports"
CALDERA_PLUGIN_APP_DIR = PROJECT_ROOT / "builder" / "caldera_plugin_app"

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


CTF_PARSER_MODULE = "plugins.ctf-exploit.app.parsers.ctf_basic"
CTF_REQUIREMENT_MODULE = "plugins.ctf-exploit.app.requirements.fact_present"

# Marker substring that recon commands emit when the vulnerability is present.
# Every recon ability that echoes this marker gets a parser that turns it into a
# fact, and the corresponding exploit ability gates on that fact.
RECON_MARKER = "VULNERABLE"

# Marker substring that goal exploit commands emit when the objective is achieved.
# Goal exploit abilities get a parser that turns this into a ctf.goal.<id> fact,
# and a generated Caldera objective references that fact for native completion
# detection.
GOAL_MARKER = "GOAL_ACHIEVED"

# Default Caldera objective ID (runs forever); we replace it with our own.
DEFAULT_OBJECTIVE_ID = "495a9828-cab1-44dd-a0ca-66e58177d8cc"


def fact_trait(module_id: str) -> str:
    """Return the fact trait used to gate exploit abilities on recon success.

    Recon abilities emit a fact with this trait (via the ctf_basic parser) when
    their output contains the VULNERABLE marker. Exploit abilities require the
    fact and reference ``#{<trait>}`` in their command so the planner only
    schedules them once recon confirmed the vulnerability.
    """
    return f"ctf.vuln.{module_id}"


def goal_fact_trait(goal_id: str) -> str:
    """Return the fact trait emitted when a goal module's exploit succeeds.

    Goal exploit abilities emit a fact with this trait (via the ctf_basic parser
    and the GOAL_ACHIEVED marker). A generated Caldera objective references it so
    objective completion reflects red team goal achievement.
    """
    return f"ctf.goal.{goal_id}"


def objective_uuid(goal_ids: tuple[str, ...]) -> str:
    """Deterministic objective ID for a set of goal module ids.

    A single-goal objective uses ``goal_ids=(goal_id,)``; the combined "all
    goals" objective uses the full tuple.
    """
    key = "_".join(sorted(goal_ids)) if goal_ids else "none"
    return str(uuid.uuid5(_NAMESPACE, f"objective_{key}"))


def _ability_requirements(section: dict) -> list[dict]:
    """Normalize a caldera section's ``requirements`` into render-ready dicts.

    Accepts either a list of {source, edge?, target?} mappings (using the
    default plugin requirement module) or explicit
    [{module, mappings: [{source, edge?, target?}]}] entries.
    """
    raw = section.get("requirements", [])
    if not raw:
        return []
    requirements: list[dict] = []
    for entry in raw:
        if isinstance(entry, dict) and "mappings" in entry:
            requirements.append(entry)
            continue
        requirements.append({
            "module": CTF_REQUIREMENT_MODULE,
            "mappings": [entry],
        })
    return requirements


def _ability_parsers(section: dict) -> list[dict]:
    """Normalize a caldera section's ``parser`` into render-ready dicts.

    Accepts a single {source, edge?, target?} dict (default plugin parser
    module) or a list of explicit [{module, mappings: [...]}] entries.
    """
    raw = section.get("parser")
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    return [{
        "module": raw.get("module", CTF_PARSER_MODULE),
        "mappings": [raw],
    }]


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
                "requirements": _ability_requirements(recon),
                "parsers": _ability_parsers(recon) or _default_recon_parsers(m.id, command),
            })

        # Exploit ability
        exploit = cal.get("exploit", {})
        if exploit.get("command"):
            exploit_payloads = exploit.get("payloads", [])
            prefixed_exploit_payloads = [f"{m.id}__{p}" for p in exploit_payloads]
            exploit_uploads = exploit.get("uploads", [])
            prefixed_exploit_uploads = [f"{m.id}__{u}" for u in exploit_uploads]
            command = exploit["command"].strip()
            for orig, prefixed in zip(exploit_payloads, prefixed_exploit_payloads):
                command = command.replace(f"{{{{ payload.{orig} }}}}", f"{{{{ payload.{prefixed} }}}}")
                command = command.replace(f"./{orig}", f"./{prefixed}")
                command = command.replace(f" {orig}", f" {prefixed}")
            for orig, prefixed in zip(exploit_uploads, prefixed_exploit_uploads):
                command = command.replace(f"{{{{ payload.{orig} }}}}", f"{{{{ payload.{prefixed} }}}}")
                command = command.replace(f"./{orig}", f"./{prefixed}")
                command = command.replace(f" {orig}", f" {prefixed}")
            has_recon = bool(recon.get("command"))
            is_goal = m.type == "goal"
            abilities.append({
                "id": _ability_uuid(m.id, "exploit"),
                "module_id": m.id,
                "name": f"Exploit: {m.name}",
                "description": exploit.get("description", f"Exploit {m.name}"),
                "tactic": tactic,
                "technique_attack_id": technique["attack_id"],
                "technique_name": technique["name"],
                "command": _gate_exploit_command(command, m.id) if has_recon else command,
                "cleanup": exploit.get("cleanup", "").strip(),
                "payloads": prefixed_exploit_payloads,
                "uploads": prefixed_exploit_uploads,
                "phase": "exploit",
                "requirements": _ability_requirements(exploit) or (
                    _default_exploit_requirement(m.id) if has_recon else []
                ),
                "parsers": _ability_parsers(exploit) or (
                    _default_goal_parsers(m.id) if is_goal else []
                ),
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
        uploads=ability.get("uploads", []),
        requirements=ability.get("requirements", []),
        parsers=ability.get("parsers", []),
    )


def _build_adversary_profiles(
    abilities: list[dict], objective_id: str | None = None
) -> list[dict]:
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
        "objective": objective_id,
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
            "objective": objective_id,
            "abilities": [
                {"id": a["id"], "comment": a["name"]}
                for a in ordered
            ],
        })

    return profiles


def _build_objectives(
    modules: list[Module],
) -> tuple[list[dict], dict[str, str], str | None]:
    """Build objective definitions from goal modules.

    Returns ``(objectives, goal_objective_map, combined_objective_id)``:
    - ``objectives`` — list of render-ready objective dicts (one per goal plus
      a combined "all goals" objective).
    - ``goal_objective_map`` — ``{goal_id: objective_id}`` for wiring per-path
      adversaries to the objective of their terminal goal.
    - ``combined_objective_id`` — objective covering every goal (used by the
      master / per-tactic adversaries); ``None`` when there are no goals.
    """
    goal_modules = [m for m in modules if m.type == "goal"]
    if not goal_modules:
        return [], {}, None

    objectives: list[dict] = []
    goal_objective_map: dict[str, str] = {}

    for m in goal_modules:
        oid = objective_uuid((m.id,))
        goal_objective_map[m.id] = oid
        objectives.append({
            "id": oid,
            "name": f"CTF Goal: {m.name}",
            "description": m.description or f"Achieve objective: {m.name}",
            "goals": [_goal_entry(m.id)],
        })

    combined_id = objective_uuid(tuple(m.id for m in goal_modules))
    objectives.append({
        "id": combined_id,
        "name": "CTF Objectives",
        "description": "Achieve all red team objectives",
        "goals": [_goal_entry(m.id) for m in goal_modules],
    })

    return objectives, goal_objective_map, combined_id


def _goal_entry(goal_id: str) -> dict:
    """A single objective goal: any fact with the goal's trait counts."""
    return {
        "target": goal_fact_trait(goal_id),
        "value": "",
        "count": 1,
        "operator": "*",
    }


def _write_plugin(
    env: Environment,
    output_dir: Path,
    abilities: list[dict],
    adversaries: list[dict],
    modules: list[Module],
    objectives: list[dict],
) -> None:
    """Write hook.py, ability YAMLs, adversary YAMLs, objectives, and payloads."""
    plugin_dir = output_dir / "plugins" / PLUGIN_NAME
    abilities_dir = plugin_dir / "data" / "abilities"
    adversaries_dir = plugin_dir / "data" / "adversaries"
    objectives_dir = plugin_dir / "data" / "objectives"
    payloads_dir = plugin_dir / "payloads"
    app_dir = plugin_dir / "app"

    for d in [abilities_dir, adversaries_dir, objectives_dir, payloads_dir]:
        d.mkdir(parents=True, exist_ok=True)

    hook_template = env.get_template("caldera_hook.py.j2")
    (plugin_dir / "hook.py").write_text(hook_template.render())

    # Stage plugin app modules (parsers, requirements) used by generated
    # abilities. Kept under plugins/<plugin>/app so Caldera can import them.
    if CALDERA_PLUGIN_APP_DIR.exists():
        shutil.copytree(CALDERA_PLUGIN_APP_DIR, app_dir, dirs_exist_ok=True)

    for ability in abilities:
        tactic_dir = abilities_dir / ability["tactic"]
        tactic_dir.mkdir(exist_ok=True)
        (tactic_dir / f"{ability['id']}.yml").write_text(_render_ability(env, ability))

    adversary_template = env.get_template("caldera_adversary.yml.j2")
    for profile in adversaries:
        content = adversary_template.render(
            adversary_id=profile["id"],
            adversary_name=profile["name"],
            adversary_description=profile["description"],
            objective=profile.get("objective"),
            abilities=profile["abilities"],
        )
        (adversaries_dir / f"{profile['id']}.yml").write_text(content)

    objective_template = env.get_template("caldera_objective.yml.j2")
    for objective in objectives:
        content = objective_template.render(
            objective_id=objective["id"],
            objective_name=objective["name"],
            objective_description=objective["description"],
            goals=objective["goals"],
        )
        (objectives_dir / f"{objective['id']}.yml").write_text(content)

    for m in modules:
        if not m.caldera:
            continue
        for section in ["recon", "exploit"]:
            section_data = m.caldera.get(section, {})
            file_names = section_data.get("payloads", []) + section_data.get("uploads", [])
            for payload in file_names:
                src = m.source_dir / payload
                if src.exists():
                    shutil.copy2(src, payloads_dir / f"{m.id}__{payload}")


def _default_recon_parsers(module_id: str, command: str) -> list[dict]:
    """Default parser for a recon ability: emit a fact when the marker is found.

    Only auto-added when the recon command emits the RECON_MARKER substring, so
    recon commands that signal success differently (or declare their own parser)
    are left untouched.
    """
    if RECON_MARKER not in command:
        return []
    return [{
        "module": CTF_PARSER_MODULE,
        "mappings": [{
            "source": fact_trait(module_id),
            "custom_parser_vals": {"marker": RECON_MARKER},
        }],
    }]


def _default_exploit_requirement(module_id: str) -> list[dict]:
    """Default requirement for an exploit ability: recon fact must exist."""
    return [{
        "module": CTF_REQUIREMENT_MODULE,
        "mappings": [{"source": fact_trait(module_id)}],
    }]


def _default_goal_parsers(goal_id: str) -> list[dict]:
    """Default parser for a goal exploit ability: emit a goal-achievement fact."""
    return [{
        "module": CTF_PARSER_MODULE,
        "mappings": [{
            "source": goal_fact_trait(goal_id),
            "custom_parser_vals": {"marker": GOAL_MARKER},
        }],
    }]


def _gate_exploit_command(command: str, module_id: str) -> str:
    """Prepend a fact-variable reference so the planner gates the exploit link.

    Caldera's planner only enforces requirements for commands that reference
    ``#{...}`` facts. Referencing the recon fact trait here causes the exploit
    link to be trimmed (not planned) until the recon fact exists — replacing the
    old ``/tmp/.ctf_phase_N`` file-based skip logic with native fact gating.
    The runtime guard is defense-in-depth in case a link slips through planning.
    """
    trait = fact_trait(module_id)
    return (
        f'[ -n "#{{{trait}}}" ] || {{ echo "SKIPPED: recon did not confirm"; exit 0; }}\n'
        f'{command}'
    )


def build_path_adversaries(
    tree: AttackTree,
    abilities: list[dict],
    vm_hostname: str = "",
    goal_objective_map: dict[str, str] | None = None,
) -> list[dict]:
    """Build per-path adversary profiles from an AttackTree.

    Each path in the tree becomes an adversary that chains the recon and exploit
    abilities for each module in the path. Paths terminating at a goal module are
    wired to that goal's objective (via ``goal_objective_map``).
    """
    goal_objective_map = goal_objective_map or {}

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
        if first_name == last_name:
            adv_name = f"CTF{hostname_part} Path {i + 1}: {first_name}"
        else:
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

        terminal_id = path[-1]
        terminal_node = tree.nodes.get(terminal_id)
        objective = (
            goal_objective_map.get(terminal_id)
            if terminal_node and terminal_node.is_goal
            else None
        )

        profiles.append({
            "id": _adversary_uuid(f"path_{vm_hostname}_{i}"),
            "name": adv_name,
            "description": f"Attack path: {' -> '.join(module_names)}",
            "objective": objective,
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

    objectives, goal_objective_map, combined_objective_id = _build_objectives(modules)

    # Path adversaries and legacy profiles share the same abilities; exploit
    # links are gated natively via recon facts + requirements, no shell markers.
    path_adversaries = build_path_adversaries(
        tree, abilities, vm_hostname, goal_objective_map
    )
    legacy_adversaries = _build_adversary_profiles(abilities, combined_objective_id)

    all_adversaries = legacy_adversaries + path_adversaries

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))

    output_dir = CALDERA_EXPORTS_DIR / export_id
    _write_plugin(env, output_dir, abilities, all_adversaries, modules, objectives)

    return output_dir, tree


def generate_caldera_export(quota: dict, export_id: str) -> Path:
    """Select modules via quota and generate a Caldera plugin export directory."""
    library = load_all_modules()
    selected = select_modules(quota, library, base_type_id=None)

    abilities = _build_abilities(selected)
    if not abilities:
        raise ValueError("No vulnerability modules with caldera metadata were selected")

    objectives, _, combined_objective_id = _build_objectives(selected)

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))

    output_dir = CALDERA_EXPORTS_DIR / export_id
    profiles = _build_adversary_profiles(abilities, combined_objective_id)
    _write_plugin(env, output_dir, abilities, profiles, selected, objectives)

    return output_dir


def generate_caldera_event_export(
    vms_modules: dict[str, list[Module]], export_id: str
) -> tuple[Path, dict[str, AttackTree]]:
    """Generate a Caldera plugin export with per-VM attack-path adversaries.

    ``vms_modules`` maps a VM hostname (or identifier) to the list of modules
    assigned to that VM. Abilities are generated once from the union of all
    VMs' modules; adversary profiles include the legacy master + per-tactic
    chains plus one adversary per distinct attack path per VM.

    Returns (output_dir, {vm_label: AttackTree}) so callers can store trees.
    """
    # Union of modules across all VMs, preserving discovery order.
    modules_by_id: dict[str, Module] = {}
    for vm_mods in vms_modules.values():
        for m in vm_mods:
            modules_by_id.setdefault(m.id, m)
    modules = list(modules_by_id.values())

    abilities = _build_abilities(modules)
    if not abilities:
        raise ValueError("No modules with caldera metadata were selected")

    objectives, goal_objective_map, combined_objective_id = _build_objectives(modules)

    # Legacy master + per-tactic adversaries from the union of abilities.
    legacy_adversaries = _build_adversary_profiles(abilities, combined_objective_id)

    # Per-VM per-path adversaries.
    path_adversaries: list[dict] = []
    trees: dict[str, AttackTree] = {}
    for vm_label, vm_mods in vms_modules.items():
        if not vm_mods:
            continue
        tree = build_attack_tree(vm_mods)
        trees[vm_label] = tree
        vm_module_ids = {m.id for m in vm_mods}
        vm_abilities = [a for a in abilities if a["module_id"] in vm_module_ids]
        path_adversaries.extend(
            build_path_adversaries(tree, vm_abilities, vm_label, goal_objective_map)
        )

    all_adversaries = legacy_adversaries + path_adversaries

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))

    output_dir = CALDERA_EXPORTS_DIR / export_id
    _write_plugin(env, output_dir, abilities, all_adversaries, modules, objectives)

    return output_dir, trees

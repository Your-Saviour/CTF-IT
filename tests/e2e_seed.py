"""Seed data for the opt-in Playwright learner journey."""

import bcrypt

from api.database import SessionLocal, init_db
from api.models import Event, Team, TeamTrainingCredential, User, VM, VMModule
from api.services.secrets import encrypt_secret

init_db()
db = SessionLocal()
event = Event(name="Browser acceptance event", quota="{}", status="open", description="Team remediation exercise")
db.add(event); db.flush()
team = Team(name="Browser Team", event_id=event.id); db.add(team); db.flush()
db.add(User(username="browser-learner", password_hash=bcrypt.hashpw(b"browser-password", bcrypt.gensalt()).decode(),
            event_id=event.id, team_id=team.id))
vm = VM(hostname="browser-target", ip_address="127.0.0.1", ssh_port=22, status="active",
        team_id=team.id, event_id=event.id)
db.add(vm); db.flush()
db.add(VMModule(vm_id=vm.id, module_id="disable_ssh_root_login", module_type="hardening",
                difficulty="medium", points=200, stage="preapplied"))
db.add(TeamTrainingCredential(team_id=team.id, username="ctf-trainee",
    private_key_encrypted=encrypt_secret("BROWSER-PRIVATE-KEY"), public_key="ssh-ed25519 BROWSER",
    sudo_password_encrypted=encrypt_secret("BROWSER-SUDO-PASSWORD"), status="active"))
db.commit(); db.close()

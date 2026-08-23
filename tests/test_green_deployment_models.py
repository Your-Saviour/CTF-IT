from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from api.database import Base
from api import models


def test_green_vm_and_deployment_state_persist_without_a_team():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    assert "green_deployment_facts" in inspect(engine).get_table_names()
    assert "green_deployment_states" in inspect(engine).get_table_names()

    with Session(engine) as db:
        event = models.Event(name="Green exercise", quota="{}", status="draft")
        db.add(event)
        db.flush()
        vm = models.VM(
            event_id=event.id,
            team_id=None,
            green_key="expo_it",
            hostname="gamenet-e1-green-expo-it",
            role="green_service",
        )
        db.add(vm)
        db.flush()
        fact = models.GreenDeploymentFact(
            event_id=event.id,
            vm_key="expo_it",
            trait="git.ssh_private_key",
            encrypted_value="enc:v1:ciphertext",
            secret=True,
        )
        state = models.GreenDeploymentState(
            vm_id=vm.id,
            module_id="expo_it",
            status="pending",
        )
        db.add_all([fact, state])
        db.commit()

        assert state.vm.green_key == "expo_it"
        assert fact.event.id == event.id


def test_generated_integration_records_reference_their_green_vm():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        event = models.Event(name="Green exercise", quota="{}")
        db.add(event)
        db.flush()
        vm = models.VM(event_id=event.id, team_id=None, green_key="expo_it")
        db.add(vm)
        db.flush()
        credential = models.ServiceCredential(
            service_name="Expo-IT event 1",
            credential_type="token",
            password="enc:v1:ciphertext",
            owner_green_vm_id=vm.id,
        )
        db.add(credential)
        db.flush()
        destination = models.IntegrationDestination(
            name="Expo-IT event 1",
            adapter_key="expo_it",
            base_url="https://10.64.0.20",
            credential_id=credential.id,
            owner_green_vm_id=vm.id,
        )
        db.add(destination)
        db.commit()

        assert destination.owner_green_vm.green_key == "expo_it"
        assert credential.owner_green_vm.green_key == "expo_it"

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from ai_agent.db import get_db
from ai_agent.db.models import StateEntity


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StateStore:
    """Persistent state store for hosts, services, credentials, etc."""

    def __init__(self, session_id: str):
        self.session_id = session_id

    def add(self, entity_type: str, data: dict, source_node_id: str | None = None) -> str:
        print(f"Adding entity: type={entity_type}, data={data}, source_node_id={source_node_id}")
        with get_db() as db:
            entity = StateEntity(
                id=str(uuid.uuid4()),
                session_id=self.session_id,
                entity_type=entity_type,
                data_json=json.dumps(data),
                source_node_id=source_node_id,
            )
            db.add(entity)
            return entity.id

    def get_by_type(self, entity_type: str) -> list[dict]:
        with get_db() as db:
            entities = db.query(StateEntity).filter(
                StateEntity.session_id == self.session_id,
                StateEntity.entity_type == entity_type,
            ).all()
            return [json.loads(e.data_json) for e in entities]

    def get_all(self) -> dict[str, list[dict]]:
        with get_db() as db:
            entities = db.query(StateEntity).filter(
                StateEntity.session_id == self.session_id,
            ).all()

        result: dict[str, list[dict]] = {}
        for e in entities:
            data = json.loads(e.data_json)
            # Aggregate by source node
            source = data.get("source_node_id", "root")
            result.setdefault(source, {}).setdefault(e.entity_type, []).append(data)
        return result

    def query(self, entity_type: str, filters: dict) -> list[dict]:
        """Query state with filters (e.g., by status, type)."""
        with get_db() as db:
            query = db.query(StateEntity).filter(
                StateEntity.session_id == self.session_id,
                StateEntity.entity_type == entity_type,
            )

            entities = query.all()
            results = []
            for entity in entities:
                data = json.loads(entity.data_json)
                matches = True
                for key, value in filters.items():
                    if data.get(key) != value:
                        matches = False
                        break
                if matches:
                    results.append(data)
            return results

    def update(self, entity_id: str, data: dict) -> None:
        with get_db() as db:
            entity = db.query(StateEntity).filter(
                StateEntity.id == entity_id,
                StateEntity.session_id == self.session_id,
            ).first()
            if entity:
                entity.data_json = json.dumps(data)
                entity.updated_at = utcnow()

    def get_success_rate(self, target: str) -> float:
        """Get historical success rate for a target."""
        actions = self.get_by_type("action_result")
        if not actions:
            return 0.5

        target_actions = [a for a in actions if a.get("target") == target]
        if not target_actions:
            return 0.5

        successes = sum(1 for a in target_actions if a.get("outcome") == "success")
        return successes / len(target_actions)

    def _restore(self, state_data: dict) -> None:
        """Restore state from checkpoint data."""
        for source_node_id, entities in state_data.items():
            for entity_type, entity_list in entities.items():
                for entity_data in entity_list:
                    self.add(entity_type, entity_data, source_node_id)
"""Persistence factory functions for optional Postgres-backed stores."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from reuleauxcoder.domain.config.models import Config, PersistenceConfig
from reuleauxcoder.infrastructure.persistence.db import create_postgres_engine
from reuleauxcoder.infrastructure.persistence.migration import run_migrations
from reuleauxcoder.infrastructure.persistence.postgres_session_store import (
    PostgresSessionStore,
)
from reuleauxcoder.infrastructure.persistence.session_store import SessionStore
from reuleauxcoder.services.agent_runtime.control_plane import AgentRuntimeControlPlane
from reuleauxcoder.services.agent_runtime.postgres_store import PostgresRuntimeStore
from reuleauxcoder.services.issue_assignment.in_memory_store import (
    InMemoryIssueAssignmentStore,
)
from reuleauxcoder.services.issue_assignment.postgres_store import (
    PostgresIssueAssignmentStore,
)
from reuleauxcoder.services.issue_assignment.service import IssueAssignmentService
from reuleauxcoder.services.taskflow.in_memory_store import InMemoryTaskflowStore
from reuleauxcoder.services.taskflow.postgres_store import PostgresTaskflowStore
from reuleauxcoder.services.taskflow.service import TaskflowService


def should_use_postgres(persistence: PersistenceConfig) -> bool:
    if persistence.backend == "memory":
        return False
    if persistence.backend == "postgres":
        return True
    return bool(persistence.database_url)


def _engine_for(config: Config) -> Any | None:
    persistence = config.persistence
    if not should_use_postgres(persistence):
        return None
    if not persistence.database_url:
        raise RuntimeError("persistence.database_url is required for Postgres backend")
    if persistence.auto_migrate:
        run_migrations(persistence.database_url)
    return create_postgres_engine(persistence.database_url)


def create_runtime_control_plane(config: Config) -> AgentRuntimeControlPlane:
    engine = _engine_for(config)
    if engine is None or not config.persistence.runtime_enabled:
        return AgentRuntimeControlPlane(
            max_running_tasks=config.agent_runtime.max_running_agents,
            runtime_snapshot=config.agent_runtime.to_runtime_snapshot(),
        )
    store = PostgresRuntimeStore(
        engine,
        max_running_tasks=config.agent_runtime.max_running_agents,
        runtime_snapshot=config.agent_runtime.to_runtime_snapshot(),
    )
    return AgentRuntimeControlPlane(
        max_running_tasks=config.agent_runtime.max_running_agents,
        runtime_snapshot=config.agent_runtime.to_runtime_snapshot(),
        store=store,
    )


def create_taskflow_service(
    config: Config, *, runtime_control_plane: AgentRuntimeControlPlane | None = None
) -> TaskflowService:
    engine = _engine_for(config)
    store = PostgresTaskflowStore(engine) if engine is not None else InMemoryTaskflowStore()
    return TaskflowService(store, runtime_control_plane=runtime_control_plane)


def create_issue_assignment_service(
    config: Config, *, taskflow_service: TaskflowService
) -> IssueAssignmentService:
    engine = _engine_for(config)
    store = (
        PostgresIssueAssignmentStore(engine)
        if engine is not None
        else InMemoryIssueAssignmentStore()
    )
    return IssueAssignmentService(store, taskflow_service=taskflow_service)


def create_session_store(config: Config, sessions_dir: Path | None) -> Any:
    engine = _engine_for(config)
    legacy_store = SessionStore(sessions_dir)
    if engine is None or not config.persistence.sessions_enabled:
        return legacy_store
    return PostgresSessionStore(
        engine,
        legacy_store=legacy_store,
        legacy_session_import=config.persistence.legacy_session_import,
    )


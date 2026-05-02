from __future__ import annotations

from reuleauxcoder.services.config.loader import ConfigLoader


def test_parse_config_reads_agent_runtime_profiles_and_agents() -> None:
    config = ConfigLoader()._parse_config(
        {
            "models": {
                "active_main": "main",
                "profiles": {"main": {"model": "gpt", "api_key": "key"}},
            },
            "agent_runtime": {
                "max_running_agents": 8,
                "max_shells_per_agent": 2,
                "runtime_profiles": {
                    "codex_remote": {
                        "executor": "codex",
                        "execution_location": "remote_server",
                        "model": "gpt-5.2-codex",
                        "command": "codex",
                        "runtime_home_policy": "per_task",
                        "config_isolation": "per_agent",
                        "credential_refs": {
                            "model": "cred_codex_team",
                            "git": "cred_github_repo_writer",
                        },
                    }
                },
                "agents": {
                    "code_reviewer": {
                        "name": "Code Reviewer",
                        "runtime_profile": "codex_remote",
                        "capabilities": ["code_review", "read_repo"],
                        "mcp": {"servers": ["github"]},
                        "skills": ["code-review"],
                        "max_concurrent_tasks": 2,
                    }
                },
            },
        }
    )

    assert config.agent_runtime.max_running_agents == 8
    assert config.agent_runtime.max_shells_per_agent == 2
    assert config.agent_runtime.runtime_profiles["codex_remote"].executor.value == "codex"
    assert (
        config.agent_runtime.runtime_profiles["codex_remote"]
        .execution_location.value
        == "remote_server"
    )
    assert (
        config.agent_runtime.runtime_profiles["codex_remote"].credential_refs["model"]
        == "cred_codex_team"
    )
    assert config.agent_runtime.agents["code_reviewer"].runtime_profile == "codex_remote"
    assert config.agent_runtime.agents["code_reviewer"].capabilities == [
        "code_review",
        "read_repo",
    ]


def test_merge_dicts_merges_agent_runtime_maps_by_id() -> None:
    loader = ConfigLoader()

    merged = loader._merge_dicts(
        {
            "agent_runtime": {
                "runtime_profiles": {
                    "codex_remote": {
                        "executor": "codex",
                        "model": "old-model",
                        "credential_refs": {"model": "cred-old"},
                    }
                },
                "agents": {
                    "reviewer": {
                        "runtime_profile": "codex_remote",
                        "capabilities": ["read_repo"],
                    }
                },
            }
        },
        {
            "agent_runtime": {
                "runtime_profiles": {
                    "codex_remote": {
                        "model": "new-model",
                        "credential_refs": {"git": "cred-git"},
                    },
                    "claude_remote": {"executor": "claude"},
                },
                "agents": {
                    "reviewer": {"capabilities": ["read_repo", "comment_issue"]},
                    "builder": {"runtime_profile": "claude_remote"},
                },
            }
        },
    )

    codex = merged["agent_runtime"]["runtime_profiles"]["codex_remote"]
    assert codex["executor"] == "codex"
    assert codex["model"] == "new-model"
    assert codex["credential_refs"] == {"model": "cred-old", "git": "cred-git"}
    assert "claude_remote" in merged["agent_runtime"]["runtime_profiles"]
    assert merged["agent_runtime"]["agents"]["reviewer"]["capabilities"] == [
        "read_repo",
        "comment_issue",
    ]
    assert "builder" in merged["agent_runtime"]["agents"]


def test_agent_runtime_snapshot_keeps_credential_refs_but_not_plaintext_secrets() -> None:
    config = ConfigLoader()._parse_config(
        {
            "models": {
                "active_main": "main",
                "profiles": {"main": {"model": "gpt", "api_key": "key"}},
            },
            "agent_runtime": {
                "runtime_profiles": {
                    "codex_remote": {
                        "executor": "codex",
                        "credential_refs": {"model": "cred_codex_team"},
                    }
                },
                "agents": {
                    "reviewer": {
                        "runtime_profile": "codex_remote",
                        "credential_refs": {"github": "cred_repo_writer"},
                    }
                },
            },
        }
    )

    snapshot = config.agent_runtime.to_runtime_snapshot()

    assert "cred_codex_team" in str(snapshot)
    assert "cred_repo_writer" in str(snapshot)
    assert "OPENAI_API_KEY" not in str(snapshot)
    assert "sk-" not in str(snapshot)


def test_config_validate_rejects_agent_referencing_missing_runtime_profile() -> None:
    config = ConfigLoader()._parse_config(
        {
            "models": {
                "active_main": "main",
                "profiles": {"main": {"model": "gpt", "api_key": "key"}},
            },
            "agent_runtime": {
                "agents": {
                    "reviewer": {
                        "runtime_profile": "missing_profile",
                        "capabilities": ["read_repo"],
                    }
                }
            },
        }
    )

    errors = config.validate()

    assert (
        "agent_runtime.agents[reviewer].runtime_profile must exist in runtime_profiles"
        in errors
    )

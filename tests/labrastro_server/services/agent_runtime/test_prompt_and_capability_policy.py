from __future__ import annotations

import importlib


def _prompt_renderer():
    return importlib.import_module("labrastro_server.services.agent_runtime.prompt_renderer")


def _policy():
    return importlib.import_module("labrastro_server.services.agent_runtime.policy")


def test_prompt_renderer_targets_executor_native_instruction_files() -> None:
    renderer_module = _prompt_renderer()

    context = renderer_module.CanonicalAgentContext(
        agent_id="code_reviewer",
        agent_name="Code Reviewer",
        agent_md=".agents/code_reviewer/AGENT.md",
        system_append="你专注于发现风险、回归和缺失测试。",
        capabilities=["code_review", "read_repo"],
        mcp_servers=["github"],
    )

    codex = renderer_module.ExecutorPromptRenderer().render("codex", context)
    claude = renderer_module.ExecutorPromptRenderer().render("claude", context)
    gemini = renderer_module.ExecutorPromptRenderer().render("gemini", context)

    assert "AGENTS.md" in codex.files
    assert "CLAUDE.md" not in codex.files
    assert "CLAUDE.md" in claude.files
    assert "GEMINI.md" in gemini.files
    assert "Code Reviewer" in codex.files["AGENTS.md"]
    assert "风险" in claude.files["CLAUDE.md"]
    assert "code_review" in gemini.files["GEMINI.md"]


def test_prompt_renderer_does_not_render_raw_secret_values() -> None:
    renderer_module = _prompt_renderer()

    context = renderer_module.CanonicalAgentContext(
        agent_id="unsafe",
        agent_name="Unsafe",
        system_append="use token sk-should-not-render",
        credential_refs={"model": "cred_codex_team"},
    )

    rendered = renderer_module.ExecutorPromptRenderer().render("codex", context)

    assert "cred_codex_team" in rendered.metadata["credential_refs"].values()
    assert "sk-should-not-render" not in str(rendered.files)


def test_platform_mcp_policy_allows_only_agent_declared_servers() -> None:
    policy_module = _policy()

    effective = policy_module.PlatformMCPPolicy(
        platform_servers={
            "github": {"command": "github-mcp", "tools": ["create_pr", "comment"]},
            "filesystem": {"command": "filesystem-mcp", "tools": ["write_file"]},
        },
        allowed_servers=["github"],
    ).render_for_agent({"servers": ["github", "filesystem"]})

    assert list(effective["servers"].keys()) == ["github"]
    assert effective["servers"]["github"]["tools"] == ["create_pr", "comment"]
    assert "filesystem" not in effective["servers"]


def test_capability_policy_blocks_undeclared_pr_creation() -> None:
    policy_module = _policy()

    policy = policy_module.AgentCapabilityPolicy(
        platform_capabilities=["read_repo", "comment_issue", "create_pr"],
        agent_capabilities=["read_repo", "comment_issue"],
    )

    assert policy.allows("read_repo") is True
    assert policy.allows("comment_issue") is True
    assert policy.allows("create_pr") is False
    assert policy.explain_denial("create_pr") == "capability not granted to agent"

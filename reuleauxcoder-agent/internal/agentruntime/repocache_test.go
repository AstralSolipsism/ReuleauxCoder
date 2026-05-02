package agentruntime

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestRepoCacheBareDirNameAvoidsRepoNameCollision(t *testing.T) {
	first := bareDirName("https://example.com/org/repo.git")
	second := bareDirName("https://example.net/other/repo.git")
	if first == second {
		t.Fatalf("bare dir names collided: %s", first)
	}
	if !strings.HasSuffix(first, ".git") || !strings.Contains(first, "repo-") {
		t.Fatalf("unexpected bare dir name: %s", first)
	}
}

func TestRepoCacheCreatesAndResetsWorktree(t *testing.T) {
	requireGit(t)
	origin := createGitRepo(t)
	cache, err := NewRepoCache(filepath.Join(t.TempDir(), "cache"))
	if err != nil {
		t.Fatal(err)
	}
	workRoot := filepath.Join(t.TempDir(), "runtime", "workspace", "task", "workdir")

	result, err := cache.CreateWorktree(context.Background(), WorktreeParams{
		WorkspaceID: "workspace/one",
		RepoURL:     origin,
		WorkDir:     workRoot,
		AgentName:   "coder",
		TaskID:      "task-1234567890",
	})
	if err != nil {
		skipIfGitSandboxBlocked(t, err)
		t.Fatal(err)
	}
	if !strings.HasPrefix(result.BranchName, "agent/coder/task-123456") {
		t.Fatalf("branch = %q", result.BranchName)
	}
	if _, err := os.Stat(filepath.Join(result.Path, "tracked.txt")); err != nil {
		t.Fatal(err)
	}
	excludePath := strings.TrimSpace(runGitOutput(t, result.Path, "rev-parse", "--git-path", "info/exclude"))
	if !filepath.IsAbs(excludePath) {
		excludePath = filepath.Join(result.Path, excludePath)
	}
	excludes, err := os.ReadFile(excludePath)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(excludes), "AGENTS.md") || !strings.Contains(string(excludes), ".codex/") {
		t.Fatalf("agent excludes missing: %s", excludes)
	}

	if err := os.WriteFile(filepath.Join(result.Path, "tracked.txt"), []byte("dirty\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(result.Path, "untracked.txt"), []byte("dirty\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	second, err := cache.CreateWorktree(context.Background(), WorktreeParams{
		WorkspaceID: "workspace/one",
		RepoURL:     origin,
		WorkDir:     workRoot,
		AgentName:   "coder",
		TaskID:      "task-1234567890",
	})
	if err != nil {
		skipIfGitSandboxBlocked(t, err)
		t.Fatal(err)
	}
	if second.Path != result.Path {
		t.Fatalf("expected reused worktree path %q, got %q", result.Path, second.Path)
	}
	content, err := os.ReadFile(filepath.Join(second.Path, "tracked.txt"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.ReplaceAll(string(content), "\r\n", "\n") != "initial\n" {
		t.Fatalf("tracked file was not reset: %q", content)
	}
	if _, err := os.Stat(filepath.Join(second.Path, "untracked.txt")); !os.IsNotExist(err) {
		t.Fatalf("untracked file was not cleaned: %v", err)
	}
}

func TestRepoCacheRetriesBranchCollision(t *testing.T) {
	requireGit(t)
	origin := createGitRepo(t)
	cache, err := NewRepoCache(filepath.Join(t.TempDir(), "cache"))
	if err != nil {
		t.Fatal(err)
	}
	first, err := cache.CreateWorktree(context.Background(), WorktreeParams{
		WorkspaceID: "workspace",
		RepoURL:     origin,
		WorkDir:     filepath.Join(t.TempDir(), "first"),
		AgentName:   "coder",
		TaskID:      "task-collision",
	})
	if err != nil {
		skipIfGitSandboxBlocked(t, err)
		t.Fatal(err)
	}
	second, err := cache.CreateWorktree(context.Background(), WorktreeParams{
		WorkspaceID: "workspace",
		RepoURL:     origin,
		WorkDir:     filepath.Join(t.TempDir(), "second"),
		AgentName:   "coder",
		TaskID:      "task-collision",
	})
	if err != nil {
		skipIfGitSandboxBlocked(t, err)
		t.Fatal(err)
	}
	if second.BranchName == first.BranchName {
		t.Fatalf("branch collision was not retried: first=%q second=%q", first.BranchName, second.BranchName)
	}
	if !strings.HasPrefix(second.BranchName, first.BranchName+"-") {
		t.Fatalf("retry branch = %q, want prefix %q", second.BranchName, first.BranchName+"-")
	}
}

func TestResolveRunCanPreparePromptFilesInsideRepoWorktree(t *testing.T) {
	requireGit(t)
	origin := createGitRepo(t)
	runtimeRoot := filepath.Join(t.TempDir(), "runtime")
	manager, resolved, err := ResolveRunWithExecEnv(
		RunRequest{
			TaskID:            "task-worktree",
			AgentID:           "coder",
			Executor:          "codex",
			ExecutionLocation: "daemon_worktree",
			Prompt:            "fix",
			Metadata: map[string]any{
				"repo_url": origin,
				"prompt_files": map[string]any{
					"AGENTS.md": "Use project conventions.\n",
				},
			},
		},
		map[string]any{},
		runtimeRoot,
		"workspace",
	)
	if err != nil {
		t.Fatal(err)
	}
	cache, err := NewRepoCache(filepath.Join(runtimeRoot, "repos"))
	if err != nil {
		t.Fatal(err)
	}
	worktree, err := cache.CreateWorktree(context.Background(), WorktreeParams{
		WorkspaceID: "workspace",
		RepoURL:     origin,
		WorkDir:     resolved.Plan.WorkDir,
		AgentName:   resolved.Request.AgentID,
		TaskID:      resolved.Request.TaskID,
	})
	if err != nil {
		skipIfGitSandboxBlocked(t, err)
		t.Fatal(err)
	}
	resolved.Request.Workdir = worktree.Path
	resolved.Request.Branch = worktree.BranchName
	prepared, err := PrepareResolvedRun(manager, resolved, PromptFilesFromMetadata(resolved.Request.Metadata))
	if err != nil {
		t.Fatal(err)
	}
	content, err := os.ReadFile(filepath.Join(prepared.Request.Workdir, "AGENTS.md"))
	if err != nil {
		t.Fatal(err)
	}
	if string(content) != "Use project conventions.\n" {
		t.Fatalf("prompt content = %q", content)
	}
	if prepared.Options.RuntimeHome == "" || !strings.Contains(prepared.Options.RuntimeHome, "codex-home") {
		t.Fatalf("CODEX_HOME was not planned: %#v", prepared.Options)
	}
	if prepared.Options.RuntimeHome == prepared.Request.Workdir {
		t.Fatalf("CODEX_HOME should stay isolated from worktree")
	}
}

func TestRepoCacheHonorsContextCancel(t *testing.T) {
	requireGit(t)
	origin := createGitRepo(t)
	cache, err := NewRepoCache(filepath.Join(t.TempDir(), "cache"))
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err = cache.CreateWorktree(ctx, WorktreeParams{
		WorkspaceID: "workspace",
		RepoURL:     origin,
		WorkDir:     filepath.Join(t.TempDir(), "work"),
		AgentName:   "coder",
		TaskID:      "task-cancel",
	})
	if err == nil {
		t.Fatal("expected context-cancelled git command to fail")
	}
}

func requireGit(t *testing.T) {
	t.Helper()
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git is not installed")
	}
}

func skipIfGitSandboxBlocked(t *testing.T, err error) {
	t.Helper()
	if strings.Contains(err.Error(), "couldn't create signal pipe") {
		t.Skipf("git subprocess is blocked by the local Windows sandbox: %v", err)
	}
}

func createGitRepo(t *testing.T) string {
	t.Helper()
	root := filepath.Join(t.TempDir(), "origin")
	if err := os.MkdirAll(root, 0o755); err != nil {
		t.Fatal(err)
	}
	runGit(t, root, "init")
	runGit(t, root, "checkout", "-B", "main")
	runGit(t, root, "config", "user.email", "test@example.invalid")
	runGit(t, root, "config", "user.name", "Test User")
	if err := os.WriteFile(filepath.Join(root, "tracked.txt"), []byte("initial\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	runGit(t, root, "add", "tracked.txt")
	runGit(t, root, "commit", "-m", "initial")
	return localRepoURL(root)
}

func localRepoURL(path string) string {
	abs, err := filepath.Abs(path)
	if err != nil {
		abs = path
	}
	return "file:///" + strings.TrimPrefix(filepath.ToSlash(abs), "/")
}

func runGit(t *testing.T, dir string, args ...string) {
	t.Helper()
	_ = runGitOutput(t, dir, args...)
}

func runGitOutput(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "GIT_TERMINAL_PROMPT=0")
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %s failed: %v\n%s", strings.Join(args, " "), err, out)
	}
	return string(out)
}

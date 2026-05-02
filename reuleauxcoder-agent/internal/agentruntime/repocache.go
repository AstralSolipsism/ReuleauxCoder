package agentruntime

import (
	"context"
	"crypto/sha1"
	"encoding/hex"
	"fmt"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

const modernFetchRefspec = "+refs/heads/*:refs/remotes/origin/*"

var agentExcludePatterns = []string{
	"AGENTS.md",
	"CLAUDE.md",
	"GEMINI.md",
	".agent_context",
	".claude/",
	".gemini/",
	".codex/",
}

type RepoInfo struct {
	URL string
}

type RepoCache struct {
	Root  string
	locks sync.Map
}

type WorktreeParams struct {
	WorkspaceID string
	RepoURL     string
	WorkDir     string
	AgentName   string
	TaskID      string
}

type WorktreeResult struct {
	Path       string
	BranchName string
	CachePath  string
	RepoURL    string
}

func NewRepoCache(root string) (*RepoCache, error) {
	if strings.TrimSpace(root) == "" {
		return nil, fmt.Errorf("repo cache root is required")
	}
	abs, err := filepath.Abs(root)
	if err != nil {
		return nil, err
	}
	return &RepoCache{Root: filepath.Clean(abs)}, nil
}

func (c *RepoCache) Sync(ctx context.Context, workspaceID string, repos []RepoInfo) error {
	workspace := safeSegment(workspaceID, "workspace")
	if err := os.MkdirAll(filepath.Join(c.Root, workspace), 0o755); err != nil {
		return err
	}
	for _, repo := range repos {
		repoURL := strings.TrimSpace(repo.URL)
		if repoURL == "" {
			continue
		}
		barePath := c.barePath(workspace, repoURL)
		unlock := c.lockFor(workspace, repoURL)
		err := c.syncOne(ctx, barePath, repoURL)
		unlock()
		if err != nil {
			return err
		}
	}
	return nil
}

func (c *RepoCache) CreateWorktree(ctx context.Context, params WorktreeParams) (*WorktreeResult, error) {
	repoURL := strings.TrimSpace(params.RepoURL)
	if repoURL == "" {
		return nil, fmt.Errorf("repo_url is required")
	}
	workspace := safeSegment(params.WorkspaceID, "workspace")
	workRoot, err := filepath.Abs(params.WorkDir)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(workRoot, 0o755); err != nil {
		return nil, err
	}
	barePath := c.barePath(workspace, repoURL)
	unlock := c.lockFor(workspace, repoURL)
	defer unlock()
	if err := c.syncOne(ctx, barePath, repoURL); err != nil {
		return nil, err
	}
	defaultBranch, err := defaultRemoteBranch(ctx, barePath)
	if err != nil {
		return nil, err
	}
	repoName := repoNameFromURL(repoURL)
	worktreePath := filepath.Join(workRoot, repoName)
	if err := assertChildPath(workRoot, worktreePath); err != nil {
		return nil, err
	}
	branch := worktreeBranchName(params.AgentName, params.TaskID)
	if isGitWorktree(ctx, worktreePath) {
		branch, err = resetExistingWorktree(ctx, worktreePath, branch, defaultBranch)
		if err != nil {
			return nil, err
		}
		if err := writeGitExcludes(ctx, worktreePath); err != nil {
			return nil, err
		}
		return &WorktreeResult{Path: worktreePath, BranchName: branch, CachePath: barePath, RepoURL: repoURL}, nil
	}
	if exists(worktreePath) {
		if err := os.RemoveAll(worktreePath); err != nil {
			return nil, err
		}
	}
	branch, err = addWorktree(ctx, barePath, worktreePath, branch, defaultBranch)
	if err != nil {
		return nil, err
	}
	if err := writeGitExcludes(ctx, worktreePath); err != nil {
		return nil, err
	}
	return &WorktreeResult{Path: worktreePath, BranchName: branch, CachePath: barePath, RepoURL: repoURL}, nil
}

func (c *RepoCache) syncOne(ctx context.Context, barePath, repoURL string) error {
	if isBareRepo(ctx, barePath) {
		if err := ensureRemoteTrackingLayout(ctx, barePath, repoURL); err != nil {
			return err
		}
		if err := gitFetch(ctx, barePath); err != nil {
			return err
		}
		_ = git(ctx, "", "--git-dir", barePath, "remote", "set-head", "origin", "--auto")
		return nil
	}
	if exists(barePath) {
		return fmt.Errorf("repo cache path exists but is not a bare repository: %s", barePath)
	}
	if err := os.MkdirAll(filepath.Dir(barePath), 0o755); err != nil {
		return err
	}
	if err := git(ctx, "", "clone", "--bare", repoURL, barePath); err != nil {
		_ = os.RemoveAll(barePath)
		return err
	}
	if err := ensureRemoteTrackingLayout(ctx, barePath, repoURL); err != nil {
		return err
	}
	if err := gitFetch(ctx, barePath); err != nil {
		return err
	}
	_ = git(ctx, "", "--git-dir", barePath, "remote", "set-head", "origin", "--auto")
	return nil
}

func (c *RepoCache) barePath(workspaceID, repoURL string) string {
	return filepath.Join(c.Root, safeSegment(workspaceID, "workspace"), bareDirName(repoURL))
}

func (c *RepoCache) lockFor(workspaceID, repoURL string) func() {
	key := workspaceID + "\x00" + repoURL
	value, _ := c.locks.LoadOrStore(key, &sync.Mutex{})
	mu := value.(*sync.Mutex)
	mu.Lock()
	return mu.Unlock
}

func gitFetch(ctx context.Context, barePath string) error {
	return git(ctx, "", "--git-dir", barePath, "fetch", "--prune", "origin", modernFetchRefspec)
}

func ensureRemoteTrackingLayout(ctx context.Context, barePath, repoURL string) error {
	if err := git(ctx, "", "--git-dir", barePath, "remote", "set-url", "origin", repoURL); err != nil {
		return err
	}
	return git(ctx, "", "--git-dir", barePath, "config", "remote.origin.fetch", modernFetchRefspec)
}

func isBareRepo(ctx context.Context, path string) bool {
	if !exists(path) {
		return false
	}
	out, err := gitOutput(ctx, "", "--git-dir", path, "rev-parse", "--is-bare-repository")
	return err == nil && strings.TrimSpace(out) == "true"
}

func defaultRemoteBranch(ctx context.Context, barePath string) (string, error) {
	out, err := gitOutput(ctx, "", "--git-dir", barePath, "symbolic-ref", "refs/remotes/origin/HEAD")
	if err == nil {
		branch := strings.TrimPrefix(strings.TrimSpace(out), "refs/remotes/origin/")
		if branch != "" && branch != "HEAD" {
			return branch, nil
		}
	}
	out, err = gitOutput(ctx, "", "--git-dir", barePath, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin")
	if err != nil {
		return "", err
	}
	for _, line := range strings.Split(out, "\n") {
		branch := strings.TrimSpace(strings.TrimPrefix(line, "origin/"))
		if branch != "" && branch != "HEAD" {
			return branch, nil
		}
	}
	return "", fmt.Errorf("unable to determine default branch for %s", barePath)
}

func addWorktree(ctx context.Context, barePath, worktreePath, branch, defaultBranch string) (string, error) {
	if err := git(ctx, "", "--git-dir", barePath, "worktree", "add", "-b", branch, worktreePath, "origin/"+defaultBranch); err == nil {
		return branch, nil
	}
	retryBranch := fmt.Sprintf("%s-%d", branch, time.Now().Unix())
	if err := git(ctx, "", "--git-dir", barePath, "worktree", "add", "-b", retryBranch, worktreePath, "origin/"+defaultBranch); err != nil {
		return "", err
	}
	return retryBranch, nil
}

func resetExistingWorktree(ctx context.Context, worktreePath, branch, defaultBranch string) (string, error) {
	if err := git(ctx, worktreePath, "fetch", "--prune", "origin", modernFetchRefspec); err != nil {
		return "", err
	}
	if err := git(ctx, worktreePath, "reset", "--hard"); err != nil {
		return "", err
	}
	if err := git(ctx, worktreePath, "clean", "-fdx"); err != nil {
		return "", err
	}
	if err := git(ctx, worktreePath, "checkout", "-B", branch, "origin/"+defaultBranch); err == nil {
		return branch, nil
	}
	retryBranch := fmt.Sprintf("%s-%d", branch, time.Now().Unix())
	if err := git(ctx, worktreePath, "checkout", "-B", retryBranch, "origin/"+defaultBranch); err != nil {
		return "", err
	}
	return retryBranch, nil
}

func writeGitExcludes(ctx context.Context, worktreePath string) error {
	excludePath, err := gitOutput(ctx, worktreePath, "rev-parse", "--git-path", "info/exclude")
	if err != nil {
		return err
	}
	excludePath = strings.TrimSpace(excludePath)
	if !filepath.IsAbs(excludePath) {
		excludePath = filepath.Join(worktreePath, excludePath)
	}
	if err := os.MkdirAll(filepath.Dir(excludePath), 0o755); err != nil {
		return err
	}
	current, _ := os.ReadFile(excludePath)
	text := string(current)
	var builder strings.Builder
	if len(current) > 0 && !strings.HasSuffix(text, "\n") {
		builder.WriteString("\n")
	}
	for _, pattern := range agentExcludePatterns {
		if strings.Contains(text, pattern) {
			continue
		}
		builder.WriteString(pattern)
		builder.WriteString("\n")
	}
	if builder.Len() == 0 {
		return nil
	}
	f, err := os.OpenFile(excludePath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = f.WriteString(builder.String())
	return err
}

func isGitWorktree(ctx context.Context, path string) bool {
	if !exists(path) {
		return false
	}
	out, err := gitOutput(ctx, path, "rev-parse", "--is-inside-work-tree")
	return err == nil && strings.TrimSpace(out) == "true"
}

func worktreeBranchName(agentName, taskID string) string {
	return "agent/" + safeSegment(agentName, "agent") + "/" + shortID(safeSegment(taskID, "task"))
}

func bareDirName(repoURL string) string {
	repo := repoNameFromURL(repoURL)
	hash := sha1.Sum([]byte(strings.TrimSpace(repoURL)))
	repoSegment := safeSegment(repo, "repo")
	if len(repoSegment) > 48 {
		repoSegment = repoSegment[:48]
	}
	return repoSegment + "-" + hex.EncodeToString(hash[:])[:10] + ".git"
}

func repoNameFromURL(repoURL string) string {
	text := strings.TrimSpace(repoURL)
	if parsed, err := url.Parse(text); err == nil && parsed.Scheme != "" {
		base := strings.TrimSuffix(filepath.Base(parsed.Path), ".git")
		return safeSegment(base, "repo")
	}
	if before, after, ok := strings.Cut(text, ":"); ok && strings.Contains(before, "@") && !strings.Contains(before, string(os.PathSeparator)) {
		base := strings.TrimSuffix(filepath.Base(after), ".git")
		return safeSegment(base, "repo")
	}
	cleaned := strings.TrimSuffix(filepath.Clean(text), string(os.PathSeparator))
	base := strings.TrimSuffix(filepath.Base(cleaned), ".git")
	return safeSegment(base, "repo")
}

func exists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

func git(ctx context.Context, dir string, args ...string) error {
	_, err := gitOutput(ctx, dir, args...)
	return err
}

func gitOutput(ctx context.Context, dir string, args ...string) (string, error) {
	cmd := exec.CommandContext(ctx, "git", args...)
	if dir != "" {
		cmd.Dir = dir
	}
	cmd.Env = append(os.Environ(), "GIT_TERMINAL_PROMPT=0")
	out, err := cmd.CombinedOutput()
	if err != nil {
		text := strings.TrimSpace(string(out))
		if text != "" {
			return string(out), fmt.Errorf("git %s failed: %w: %s", strings.Join(args, " "), err, text)
		}
		return string(out), fmt.Errorf("git %s failed: %w", strings.Join(args, " "), err)
	}
	return string(out), nil
}

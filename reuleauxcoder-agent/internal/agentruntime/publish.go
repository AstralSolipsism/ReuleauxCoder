package agentruntime

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"strings"
)

type PublishOptions struct {
	EventSink func(Event)
}

type PublishResult struct {
	Artifacts []map[string]any
	Events    []Event
}

func PublishWorktree(ctx context.Context, req RunRequest, opts PublishOptions) PublishResult {
	publisher := worktreePublisher{ctx: ctx, req: req, opts: opts}
	return publisher.run()
}

type worktreePublisher struct {
	ctx    context.Context
	req    RunRequest
	opts   PublishOptions
	result PublishResult
}

func (p *worktreePublisher) run() PublishResult {
	workdir := strings.TrimSpace(p.req.Workdir)
	branch := strings.TrimSpace(p.req.Branch)
	if workdir == "" {
		p.fail("push", "workdir is required for worktree publish")
		return p.result
	}
	if branch == "" {
		current, err := gitOutput(p.ctx, workdir, "rev-parse", "--abbrev-ref", "HEAD")
		if err != nil {
			p.fail("push", err.Error())
			return p.result
		}
		branch = strings.TrimSpace(current)
	}

	p.emit(Event{
		Type: EventStatus,
		Data: map[string]any{
			"status":  "publishing_branch",
			"workdir": workdir,
			"branch":  branch,
		},
	})
	status, err := gitOutput(p.ctx, workdir, "status", "--porcelain", "--untracked-files=all")
	if err != nil {
		p.fail("push", err.Error())
		return p.result
	}
	if strings.TrimSpace(status) == "" {
		p.emit(Event{
			Type: EventStatus,
			Data: map[string]any{
				"status":     "publish_skipped",
				"branch":     branch,
				"no_changes": true,
			},
		})
		p.result.Artifacts = append(p.result.Artifacts, map[string]any{
			"type":        "report",
			"status":      "generated",
			"branch_name": branch,
			"content":     "No worktree changes detected.",
			"metadata": map[string]any{
				"no_changes": true,
				"workdir":    workdir,
			},
		})
		return p.result
	}

	if err := git(p.ctx, workdir, "add", "-A"); err != nil {
		p.fail("push", err.Error())
		return p.result
	}
	authorName := metadataString(p.req.Metadata, "git_author_name")
	if authorName == "" {
		authorName = "EZCode Agent"
	}
	authorEmail := metadataString(p.req.Metadata, "git_author_email")
	if authorEmail == "" {
		authorEmail = "agent@ezcode.local"
	}
	message := metadataString(p.req.Metadata, "commit_message")
	if message == "" {
		message = "agent: " + p.req.TaskID
	}
	if err := git(
		p.ctx,
		workdir,
		"-c", "user.name="+authorName,
		"-c", "user.email="+authorEmail,
		"commit", "-m", message,
	); err != nil {
		p.fail("push", err.Error())
		return p.result
	}
	if err := git(p.ctx, workdir, "push", "-u", "origin", branch); err != nil {
		p.fail("push", err.Error())
		return p.result
	}

	repoURL := metadataString(p.req.Metadata, "repo_url")
	if repoURL == "" {
		repoURL, _ = gitOutput(p.ctx, workdir, "remote", "get-url", "origin")
		repoURL = strings.TrimSpace(repoURL)
	}
	p.emit(Event{
		Type: EventStatus,
		Data: map[string]any{
			"status":   "branch_pushed",
			"branch":   branch,
			"repo_url": repoURL,
		},
	})
	p.result.Artifacts = append(p.result.Artifacts, map[string]any{
		"type":        "branch",
		"status":      "pushed",
		"branch_name": branch,
		"path":        workdir,
		"metadata": map[string]any{
			"repo_url": repoURL,
			"workdir":  workdir,
		},
	})

	if !metadataBoolDefault(p.req.Metadata, "pr_enabled", true) {
		p.emit(Event{
			Type: EventStatus,
			Data: map[string]any{
				"status":     "pr_skipped",
				"branch":     branch,
				"pr_enabled": false,
			},
		})
		return p.result
	}
	if err := p.publishPR(workdir, branch); err != nil {
		p.fail("pr_create", err.Error())
	}
	return p.result
}

func (p *worktreePublisher) publishPR(workdir, branch string) error {
	provider := metadataString(p.req.Metadata, "pr_provider")
	if provider == "" {
		provider = "github_gh"
	}
	if provider != "github_gh" {
		return fmt.Errorf("unsupported pr_provider: %s", provider)
	}
	base := metadataString(p.req.Metadata, "pr_base")
	if base == "" {
		resolved, err := remoteDefaultBranchFromWorktree(p.ctx, workdir)
		if err != nil {
			return err
		}
		base = resolved
	}
	title := metadataString(p.req.Metadata, "pr_title")
	if title == "" {
		title = "Agent task " + p.req.TaskID
	}
	body := metadataString(p.req.Metadata, "pr_body")
	if body == "" {
		body = defaultPRBody(p.req, workdir, branch)
	}

	if url, err := ghOutput(p.ctx, workdir, "pr", "view", branch, "--json", "url", "--jq", ".url"); err == nil {
		prURL := strings.TrimSpace(url)
		if prURL != "" {
			p.emit(Event{
				Type: EventStatus,
				Data: map[string]any{
					"status": "pr_created",
					"branch": branch,
					"pr_url": prURL,
					"reused": true,
				},
			})
			p.result.Artifacts = append(p.result.Artifacts, pullRequestArtifact(branch, prURL, provider, base, true))
			return nil
		}
	}

	url, err := ghOutput(
		p.ctx,
		workdir,
		"pr", "create",
		"--head", branch,
		"--base", base,
		"--title", title,
		"--body", body,
	)
	if err != nil {
		return err
	}
	prURL := strings.TrimSpace(url)
	if prURL == "" {
		return fmt.Errorf("gh pr create returned empty URL")
	}
	p.emit(Event{
		Type: EventStatus,
		Data: map[string]any{
			"status": "pr_created",
			"branch": branch,
			"pr_url": prURL,
			"reused": false,
		},
	})
	p.result.Artifacts = append(p.result.Artifacts, pullRequestArtifact(branch, prURL, provider, base, false))
	return nil
}

func (p *worktreePublisher) emit(event Event) {
	p.result.Events = append(p.result.Events, event)
	if p.opts.EventSink != nil {
		p.opts.EventSink(event)
	}
}

func (p *worktreePublisher) fail(stage, message string) {
	p.emit(Event{
		Type: EventStatus,
		Data: map[string]any{
			"status": "publish_failed",
			"stage":  stage,
			"error":  message,
		},
	})
	p.result.Artifacts = append(p.result.Artifacts, map[string]any{
		"type":    "log",
		"status":  "failed",
		"content": message,
		"metadata": map[string]any{
			"stage": stage,
		},
	})
}

func pullRequestArtifact(branch, prURL, provider, base string, reused bool) map[string]any {
	return map[string]any{
		"type":        "pull_request",
		"status":      "pr_created",
		"branch_name": branch,
		"pr_url":      prURL,
		"metadata": map[string]any{
			"provider": provider,
			"base":     base,
			"reused":   reused,
		},
	}
}

func defaultPRBody(req RunRequest, workdir, branch string) string {
	lines := []string{
		"Agent runtime task completed.",
		"",
		"Task: " + req.TaskID,
		"Issue: " + req.IssueID,
		"Agent: " + req.AgentID,
		"Branch: " + branch,
		"Workdir: " + workdir,
	}
	return strings.Join(lines, "\n")
}

func remoteDefaultBranchFromWorktree(ctx context.Context, workdir string) (string, error) {
	out, err := gitOutput(ctx, workdir, "symbolic-ref", "refs/remotes/origin/HEAD")
	if err == nil {
		branch := strings.TrimPrefix(strings.TrimSpace(out), "refs/remotes/origin/")
		if branch != "" && branch != "HEAD" {
			return branch, nil
		}
	}
	out, err = gitOutput(ctx, workdir, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin")
	if err != nil {
		return "", err
	}
	for _, line := range strings.Split(out, "\n") {
		branch := strings.TrimSpace(strings.TrimPrefix(line, "origin/"))
		if branch != "" && branch != "HEAD" {
			return branch, nil
		}
	}
	return "", fmt.Errorf("unable to determine remote default branch for %s", workdir)
}

func ghOutput(ctx context.Context, dir string, args ...string) (string, error) {
	cmd := exec.CommandContext(ctx, "gh", args...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "GIT_TERMINAL_PROMPT=0")
	out, err := cmd.CombinedOutput()
	if err != nil {
		text := strings.TrimSpace(string(out))
		if text != "" {
			return string(out), fmt.Errorf("gh %s failed: %w: %s", strings.Join(args, " "), err, text)
		}
		return string(out), fmt.Errorf("gh %s failed: %w", strings.Join(args, " "), err)
	}
	return string(out), nil
}

func metadataString(metadata map[string]any, key string) string {
	if len(metadata) == 0 {
		return ""
	}
	if value, ok := metadata[key]; ok && value != nil {
		return strings.TrimSpace(fmt.Sprint(value))
	}
	return ""
}

func metadataBoolDefault(metadata map[string]any, key string, fallback bool) bool {
	if len(metadata) == 0 {
		return fallback
	}
	value, ok := metadata[key]
	if !ok || value == nil {
		return fallback
	}
	switch typed := value.(type) {
	case bool:
		return typed
	case string:
		text := strings.TrimSpace(strings.ToLower(typed))
		if text == "" {
			return fallback
		}
		return !(text == "0" || text == "false" || text == "no" || text == "off")
	default:
		text := strings.TrimSpace(strings.ToLower(fmt.Sprint(value)))
		if text == "" {
			return fallback
		}
		return !(text == "0" || text == "false" || text == "no" || text == "off")
	}
}

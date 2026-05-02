package agentruntime

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

var unsafeSegmentRE = regexp.MustCompile(`[^a-zA-Z0-9._-]+`)

type ExecEnvPlan struct {
	RuntimeRoot string
	WorkspaceID string
	TaskID      string
	AgentID     string
	RootDir     string
	WorkDir     string
	OutputDir   string
	LogDir      string
	CodexHome   string
	BranchName  string
}

type ExecEnvManager struct {
	RuntimeRoot string
}

func NewExecEnvManager(runtimeRoot string) (*ExecEnvManager, error) {
	if strings.TrimSpace(runtimeRoot) == "" {
		return nil, fmt.Errorf("runtime root is required")
	}
	root, err := filepath.Abs(runtimeRoot)
	if err != nil {
		return nil, err
	}
	return &ExecEnvManager{RuntimeRoot: filepath.Clean(root)}, nil
}

func (m *ExecEnvManager) Plan(workspaceID, taskID, agentID string) (ExecEnvPlan, error) {
	workspace := safeSegment(workspaceID, "workspace")
	task := safeSegment(taskID, "task")
	agent := safeSegment(agentID, "agent")
	root := filepath.Join(m.RuntimeRoot, workspace, shortID(task))
	plan := ExecEnvPlan{
		RuntimeRoot: m.RuntimeRoot,
		WorkspaceID: workspace,
		TaskID:      task,
		AgentID:     agent,
		RootDir:     root,
		WorkDir:     filepath.Join(root, "workdir"),
		OutputDir:   filepath.Join(root, "output"),
		LogDir:      filepath.Join(root, "logs"),
		CodexHome:   filepath.Join(root, "codex-home"),
		BranchName:  "agent/" + agent + "/" + shortID(task),
	}
	for _, p := range []string{plan.RootDir, plan.WorkDir, plan.OutputDir, plan.LogDir, plan.CodexHome} {
		if err := m.AssertOwned(p); err != nil {
			return ExecEnvPlan{}, err
		}
	}
	return plan, nil
}

func (m *ExecEnvManager) Prepare(plan ExecEnvPlan, promptFiles map[string]string) error {
	return m.PrepareWorkDir(plan, plan.WorkDir, promptFiles)
}

func (m *ExecEnvManager) PrepareWorkDir(plan ExecEnvPlan, workdir string, promptFiles map[string]string) error {
	if strings.TrimSpace(workdir) == "" {
		workdir = plan.WorkDir
	}
	if err := m.AssertOwned(workdir); err != nil {
		return err
	}
	plan.WorkDir = workdir
	for _, p := range []string{plan.WorkDir, plan.OutputDir, plan.LogDir, plan.CodexHome} {
		if err := m.AssertOwned(p); err != nil {
			return err
		}
		if err := os.MkdirAll(p, 0o755); err != nil {
			return err
		}
	}
	for name, content := range promptFiles {
		cleanName := filepath.Clean(name)
		if strings.HasPrefix(cleanName, "..") || filepath.IsAbs(cleanName) {
			return fmt.Errorf("prompt file escapes workdir: %s", name)
		}
		target := filepath.Join(plan.WorkDir, cleanName)
		if err := assertChildPath(plan.WorkDir, target); err != nil {
			return err
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		if err := os.WriteFile(target, []byte(content), 0o600); err != nil {
			return err
		}
	}
	return nil
}

func (m *ExecEnvManager) PrepareRuntimeDirs(plan ExecEnvPlan) error {
	for _, p := range []string{plan.OutputDir, plan.LogDir, plan.CodexHome} {
		if err := m.AssertOwned(p); err != nil {
			return err
		}
		if err := os.MkdirAll(p, 0o755); err != nil {
			return err
		}
	}
	return nil
}

func (m *ExecEnvManager) AssertOwned(path string) error {
	root, err := filepath.Abs(m.RuntimeRoot)
	if err != nil {
		return err
	}
	target, err := filepath.Abs(path)
	if err != nil {
		return err
	}
	rel, err := filepath.Rel(root, target)
	if err != nil {
		return err
	}
	if rel == "." || strings.HasPrefix(rel, ".."+string(os.PathSeparator)) || rel == ".." {
		return fmt.Errorf("path is outside runtime root: %s", target)
	}
	return nil
}

func safeSegment(value, fallback string) string {
	text := unsafeSegmentRE.ReplaceAllString(strings.TrimSpace(value), "-")
	text = strings.Trim(text, ".-_/\\")
	if text == "" {
		return fallback
	}
	if len(text) > 64 {
		return text[:64]
	}
	return text
}

func shortID(value string) string {
	if len(value) <= 12 {
		return value
	}
	return value[:12]
}

func assertChildPath(root, path string) error {
	rootAbs, err := filepath.Abs(root)
	if err != nil {
		return err
	}
	targetAbs, err := filepath.Abs(path)
	if err != nil {
		return err
	}
	rel, err := filepath.Rel(rootAbs, targetAbs)
	if err != nil {
		return err
	}
	if rel == "." || strings.HasPrefix(rel, ".."+string(os.PathSeparator)) || rel == ".." {
		return fmt.Errorf("path is outside workdir: %s", targetAbs)
	}
	return nil
}

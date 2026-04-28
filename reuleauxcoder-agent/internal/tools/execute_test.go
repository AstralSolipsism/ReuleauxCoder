package tools

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/RC-CHN/ReuleauxCoder/reuleauxcoder-agent/internal/protocol"
)

func TestBuildShellCommandUsesShOutsideWindows(t *testing.T) {
	shell, args := buildShellCommand("echo hi", "linux", func(string) (string, error) {
		return "", errors.New("unused")
	})

	if shell != "sh" {
		t.Fatalf("shell = %q, want sh", shell)
	}
	wantArgs := []string{"-lc", "echo hi"}
	if !reflect.DeepEqual(args, wantArgs) {
		t.Fatalf("args = %#v, want %#v", args, wantArgs)
	}
}

func TestBuildShellCommandPrefersBashOnWindows(t *testing.T) {
	shell, args := buildShellCommand("echo hi", "windows", func(name string) (string, error) {
		if name == "bash" {
			return "C:/Program Files/Git/bin/bash.exe", nil
		}
		return "", errors.New("not found")
	})

	if shell != "C:/Program Files/Git/bin/bash.exe" {
		t.Fatalf("shell = %q, want Git Bash", shell)
	}
	wantArgs := []string{"-c", "echo hi"}
	if !reflect.DeepEqual(args, wantArgs) {
		t.Fatalf("args = %#v, want %#v", args, wantArgs)
	}
}

func TestBuildShellCommandFallsBackToPwshOnWindows(t *testing.T) {
	shell, args := buildShellCommand("echo hi", "windows", func(name string) (string, error) {
		if name == "bash" || name == "bash.exe" {
			return "C:/Windows/System32/bash.exe", nil
		}
		if name == "pwsh" {
			return "C:/Program Files/PowerShell/7/pwsh.exe", nil
		}
		return "", errors.New("not found")
	})

	if shell != "C:/Program Files/PowerShell/7/pwsh.exe" {
		t.Fatalf("shell = %q, want pwsh path", shell)
	}
	wantArgs := []string{"-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", "echo hi"}
	if !reflect.DeepEqual(args, wantArgs) {
		t.Fatalf("args = %#v, want %#v", args, wantArgs)
	}
}

func TestBuildShellCommandFallsBackToWindowsPowerShell(t *testing.T) {
	shell, args := buildShellCommand("echo a && echo b", "windows", func(name string) (string, error) {
		if name == "powershell.exe" {
			return "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe", nil
		}
		return "", errors.New("not found")
	})

	if shell != "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe" {
		t.Fatalf("shell = %q, want powershell.exe path", shell)
	}
	if got := args[len(args)-1]; got != "echo a ; echo b" {
		t.Fatalf("normalized command = %q, want %q", got, "echo a ; echo b")
	}
}

func TestExecuteShellReturnsRemoteCancelledWhenContextCancelled(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	result := ExecuteWithContext(ctx, protocol.ExecToolRequest{
		ToolName:   "shell",
		Args:       map[string]any{"command": "echo should-not-run"},
		TimeoutSec: 30,
	}, t.TempDir(), nil)

	if result.OK || result.ErrorCode != "REMOTE_CANCELLED" {
		t.Fatalf("result = %#v, want REMOTE_CANCELLED", result)
	}
}

func TestPreviewWriteFileDoesNotWriteAndExecuteDetectsStaleFile(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "notes.txt")
	if err := os.WriteFile(target, []byte("old\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	req := protocol.ToolPreviewRequest{
		ToolName: "write_file",
		Args: map[string]any{
			"file_path": "notes.txt",
			"content":   "new\n",
		},
	}
	preview := Preview(req, dir)
	if !preview.OK {
		t.Fatalf("preview failed: %s", preview.ErrorMessage)
	}
	if got := readFileForTest(t, target); got != "old\n" {
		t.Fatalf("preview wrote file, got %q", got)
	}
	if !strings.Contains(preview.Diff, "-old") || !strings.Contains(preview.Diff, "+new") {
		t.Fatalf("preview diff = %q", preview.Diff)
	}
	if preview.OriginalText != "old\n" || preview.ModifiedText != "new\n" {
		t.Fatalf("preview texts = %q -> %q", preview.OriginalText, preview.ModifiedText)
	}

	if err := os.WriteFile(target, []byte("changed\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	result := Execute(protocol.ExecToolRequest{
		ToolName:      "write_file",
		Args:          req.Args,
		ExpectedState: expectedStateFromPreview(preview),
	}, dir, nil)
	if result.OK || result.ErrorCode != "REMOTE_TOOL_STALE_PREVIEW" {
		t.Fatalf("result = %#v, want stale preview error", result)
	}
	if got := readFileForTest(t, target); got != "changed\n" {
		t.Fatalf("stale execute changed file, got %q", got)
	}
}

func TestPreviewAndExecuteEditFileShareValidationAndState(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "main.txt")
	if err := os.WriteFile(target, []byte("alpha beta\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	preview := Preview(protocol.ToolPreviewRequest{
		ToolName: "edit_file",
		Args: map[string]any{
			"file_path":  "main.txt",
			"old_string": "alpha",
			"new_string": "omega",
		},
	}, dir)
	if !preview.OK {
		t.Fatalf("preview failed: %s", preview.ErrorMessage)
	}
	if got := readFileForTest(t, target); got != "alpha beta\n" {
		t.Fatalf("preview edited file, got %q", got)
	}

	result := Execute(protocol.ExecToolRequest{
		ToolName: "edit_file",
		Args: map[string]any{
			"file_path":  "main.txt",
			"old_string": "alpha",
			"new_string": "omega",
		},
		ExpectedState: expectedStateFromPreview(preview),
	}, dir, nil)
	if !result.OK {
		t.Fatalf("execute failed: %#v", result)
	}
	if !strings.Contains(result.Result, "--- a/main.txt") || !strings.Contains(result.Result, "+omega beta") {
		t.Fatalf("execute diff = %q", result.Result)
	}
	if got := readFileForTest(t, target); got != "omega beta\n" {
		t.Fatalf("execute content = %q", got)
	}
}

func TestPreviewEditFileRejectsDuplicateOldString(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "main.txt")
	if err := os.WriteFile(target, []byte("same same\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	preview := Preview(protocol.ToolPreviewRequest{
		ToolName: "edit_file",
		Args: map[string]any{
			"file_path":  "main.txt",
			"old_string": "same",
			"new_string": "other",
		},
	}, dir)
	if preview.OK || !strings.Contains(preview.ErrorMessage, "appears 2 times") {
		t.Fatalf("preview = %#v, want duplicate old_string error", preview)
	}
	if got := readFileForTest(t, target); got != "same same\n" {
		t.Fatalf("failed preview changed file, got %q", got)
	}
}

func TestExecuteFallsBackWhenRequestedCWDIsStale(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "notes.txt"), []byte("hello\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	stale := filepath.Join(dir, "missing")

	result := Execute(protocol.ExecToolRequest{
		ToolName: "read_file",
		CWD:      &stale,
		Args:     map[string]any{"file_path": "notes.txt"},
	}, dir, nil)

	if !result.OK {
		t.Fatalf("execute failed: %#v", result)
	}
	if !strings.Contains(result.Result, "Warning: working directory no longer exists") {
		t.Fatalf("missing stale cwd warning: %q", result.Result)
	}
	if !strings.Contains(result.Result, "1\thello") {
		t.Fatalf("read result = %q", result.Result)
	}
}

func TestReadFileUsesOffsetAndLimitWithoutReadingFullFile(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "notes.txt")
	if err := os.WriteFile(target, []byte("one\ntwo\nthree\nfour\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	result := Execute(protocol.ExecToolRequest{
		ToolName: "read_file",
		Args: map[string]any{
			"file_path": "notes.txt",
			"offset":    2,
			"limit":     2,
		},
	}, dir, nil)

	if !result.OK {
		t.Fatalf("read failed: %#v", result)
	}
	if !strings.Contains(result.Result, "2\ttwo") || !strings.Contains(result.Result, "3\tthree") {
		t.Fatalf("read result = %q", result.Result)
	}
	if strings.Contains(result.Result, "1\tone") || strings.Contains(result.Result, "4\tfour") {
		t.Fatalf("read leaked outside requested range: %q", result.Result)
	}
}

func TestGlobSupportsGlobstarAndSortsByNewestMtime(t *testing.T) {
	dir := t.TempDir()
	rootFile := filepath.Join(dir, "root.txt")
	nestedDir := filepath.Join(dir, "nested")
	if err := os.Mkdir(nestedDir, 0o755); err != nil {
		t.Fatal(err)
	}
	nestedFile := filepath.Join(nestedDir, "newer.txt")
	if err := os.WriteFile(rootFile, []byte("root"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(nestedFile, []byte("nested"), 0o644); err != nil {
		t.Fatal(err)
	}
	oldTime := time.Now().Add(-2 * time.Hour)
	newTime := time.Now().Add(-1 * time.Hour)
	if err := os.Chtimes(rootFile, oldTime, oldTime); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(nestedFile, newTime, newTime); err != nil {
		t.Fatal(err)
	}

	result := Execute(protocol.ExecToolRequest{
		ToolName: "glob",
		Args: map[string]any{
			"pattern": "**/*.txt",
			"path":    ".",
		},
	}, dir, nil)

	if !result.OK {
		t.Fatalf("glob failed: %#v", result)
	}
	lines := strings.Split(result.Result, "\n")
	if len(lines) < 2 {
		t.Fatalf("glob result = %q, want two matches", result.Result)
	}
	if lines[0] != nestedFile || lines[1] != rootFile {
		t.Fatalf("glob order = %#v, want newest first", lines)
	}
}

func TestTruncateOutputKeepsHeadAndTail(t *testing.T) {
	out := strings.Repeat("h", maxOutputChars) + strings.Repeat("t", keepTailChars+100)
	truncated := truncateOutput(out)

	if len(truncated) >= len(out) {
		t.Fatalf("output was not truncated")
	}
	if !strings.Contains(truncated, "... truncated (") {
		t.Fatalf("missing truncation marker: %q", truncated)
	}
	if !strings.HasPrefix(truncated, strings.Repeat("h", 20)) || !strings.HasSuffix(truncated, strings.Repeat("t", 20)) {
		t.Fatalf("truncated output did not preserve head and tail")
	}
}

func expectedStateFromPreview(preview protocol.ToolPreviewResult) map[string]any {
	state := map[string]any{
		"resolved_path": preview.ResolvedPath,
		"old_sha256":    preview.OldSHA256,
	}
	if preview.OldExists != nil {
		state["old_exists"] = *preview.OldExists
	}
	if preview.OldSize != nil {
		state["old_size"] = *preview.OldSize
	}
	if preview.OldMTimeNS != nil {
		state["old_mtime_ns"] = *preview.OldMTimeNS
	}
	return state
}

func readFileForTest(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

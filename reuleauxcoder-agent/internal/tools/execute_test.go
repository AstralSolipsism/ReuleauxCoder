package tools

import (
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

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

func TestBuildShellCommandPrefersPwshOnWindows(t *testing.T) {
	shell, args := buildShellCommand("echo hi", "windows", func(name string) (string, error) {
		if name == "pwsh" {
			return "C:/Program Files/PowerShell/7/pwsh.exe", nil
		}
		return "", errors.New("not found")
	})

	if shell != "pwsh" {
		t.Fatalf("shell = %q, want pwsh", shell)
	}
	wantArgs := []string{
		"-NoProfile",
		"-NonInteractive",
		"-ExecutionPolicy",
		"Bypass",
		"-Command",
		"echo hi",
	}
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

	if shell != "powershell.exe" {
		t.Fatalf("shell = %q, want powershell.exe", shell)
	}
	if got := args[len(args)-1]; got != "echo a ; echo b" {
		t.Fatalf("normalized command = %q, want %q", got, "echo a ; echo b")
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

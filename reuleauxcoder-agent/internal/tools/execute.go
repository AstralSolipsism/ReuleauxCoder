package tools

import (
	"bytes"
	"context"
	"crypto/sha256"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/RC-CHN/ReuleauxCoder/reuleauxcoder-agent/internal/protocol"
)

var skipDirs = map[string]struct{}{
	".git":         {},
	"node_modules": {},
	"__pycache__":  {},
	".venv":        {},
	"venv":         {},
	".tox":         {},
	"dist":         {},
	"build":        {},
}

func Execute(
	req protocol.ExecToolRequest,
	currentCWD string,
	onStream func(protocol.ToolStreamChunk),
) protocol.ExecToolResult {
	cwd := currentCWD
	if req.CWD != nil && *req.CWD != "" {
		cwd = *req.CWD
	}

	switch req.ToolName {
	case "shell":
		return runShell(req.Args, cwd, req.TimeoutSec, onStream)
	case "read_file":
		return readFile(req.Args, cwd)
	case "write_file":
		return writeFile(req.Args, cwd, req.ExpectedState)
	case "edit_file":
		return editFile(req.Args, cwd, req.ExpectedState)
	case "glob":
		return globFiles(req.Args, cwd)
	case "grep":
		return grepFiles(req.Args, cwd)
	default:
		return errorResult("REMOTE_TOOL_ERROR", fmt.Sprintf("unsupported tool %q", req.ToolName))
	}
}

func Preview(req protocol.ToolPreviewRequest, currentCWD string) protocol.ToolPreviewResult {
	cwd := currentCWD
	if req.CWD != nil && *req.CWD != "" {
		cwd = *req.CWD
	}
	if req.ToolName != "write_file" && req.ToolName != "edit_file" {
		return protocol.ToolPreviewResult{
			OK:           false,
			ErrorCode:    "REMOTE_TOOL_PREVIEW_UNSUPPORTED",
			ErrorMessage: fmt.Sprintf("tool %q does not support preview", req.ToolName),
		}
	}
	mutation, err := buildFileMutation(req.ToolName, req.Args, cwd)
	if err != nil {
		return protocol.ToolPreviewResult{
			OK:           false,
			ErrorCode:    "REMOTE_TOOL_ERROR",
			ErrorMessage: err.Error(),
		}
	}
	oldExists := mutation.oldState.exists
	oldSize := mutation.oldState.size
	oldMTimeNS := mutation.oldState.mtimeNS
	section := map[string]any{
		"id":            "diff",
		"title":         mutation.diffTitle(),
		"kind":          "diff",
		"content":       mutation.diff,
		"path":          mutation.filePath,
		"resolved_path": mutation.resolvedPath,
	}
	originalText, modifiedText := previewTexts(mutation.oldContent, mutation.newContent)
	if originalText != "" || modifiedText != "" {
		section["original_text"] = originalText
		section["modified_text"] = modifiedText
	}
	return protocol.ToolPreviewResult{
		OK:           true,
		Sections:     []map[string]any{section},
		ResolvedPath: mutation.resolvedPath,
		OldSHA256:    mutation.oldState.sha256,
		OldExists:    &oldExists,
		OldSize:      &oldSize,
		OldMTimeNS:   &oldMTimeNS,
		Diff:         mutation.diff,
		OriginalText: originalText,
		ModifiedText: modifiedText,
	}
}

func runShell(
	args map[string]any,
	cwd string,
	timeoutSec int,
	onStream func(protocol.ToolStreamChunk),
) protocol.ExecToolResult {
	command, ok := args["command"].(string)
	if !ok || strings.TrimSpace(command) == "" {
		return errorResult("REMOTE_TOOL_ERROR", "shell command must be a non-empty string")
	}
	if timeout, ok := asInt(args["timeout"]); ok && timeout > 0 {
		timeoutSec = timeout
	}
	if timeoutSec <= 0 {
		timeoutSec = 120
	}

	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeoutSec)*time.Second)
	defer cancel()

	shell, shellArgs := buildShellCommand(command, runtime.GOOS, exec.LookPath)
	cmd := exec.CommandContext(ctx, shell, shellArgs...)
	cmd.Dir = cwd

	stdoutPipe, err := cmd.StdoutPipe()
	if err != nil {
		return errorResult("REMOTE_TOOL_ERROR", err.Error())
	}
	stderrPipe, err := cmd.StderrPipe()
	if err != nil {
		return errorResult("REMOTE_TOOL_ERROR", err.Error())
	}

	if err := cmd.Start(); err != nil {
		return errorResult("REMOTE_TOOL_ERROR", err.Error())
	}

	var stdoutBuf bytes.Buffer
	var stderrBuf bytes.Buffer
	var mu sync.Mutex
	var wg sync.WaitGroup
	readStream := func(r io.Reader, kind string, target *bytes.Buffer) {
		defer wg.Done()
		buf := make([]byte, 4096)
		for {
			n, readErr := r.Read(buf)
			if n > 0 {
				chunk := string(buf[:n])
				mu.Lock()
				target.WriteString(chunk)
				mu.Unlock()
				if onStream != nil {
					onStream(protocol.ToolStreamChunk{ChunkType: kind, Data: chunk})
				}
			}
			if readErr != nil {
				if readErr == io.EOF {
					return
				}
				return
			}
		}
	}

	wg.Add(2)
	go readStream(stdoutPipe, "stdout", &stdoutBuf)
	go readStream(stderrPipe, "stderr", &stderrBuf)

	err = cmd.Wait()
	wg.Wait()

	if ctx.Err() == context.DeadlineExceeded {
		return errorResult("REMOTE_TIMEOUT", fmt.Sprintf("Remote execution timed out after %ds", timeoutSec))
	}

	out := stdoutBuf.String()
	if stderrBuf.Len() > 0 {
		if out != "" {
			out += "\n"
		}
		out += "[stderr]\n" + stderrBuf.String()
	}
	exitCode := 0
	if err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			exitCode = exitErr.ExitCode()
			if out != "" {
				out += "\n"
			}
			out += fmt.Sprintf("[exit code: %d]", exitCode)
		} else {
			return errorResult("REMOTE_TOOL_ERROR", err.Error())
		}
	}
	if strings.TrimSpace(out) == "" {
		out = "(no output)"
	}
	return protocol.ExecToolResult{OK: true, Result: out, Meta: map[string]any{"exit_code": exitCode}}
}

func buildShellCommand(
	command string,
	goos string,
	lookPath func(string) (string, error),
) (string, []string) {
	if goos != "windows" {
		return "sh", []string{"-lc", command}
	}

	shell := "powershell.exe"
	if _, err := lookPath("pwsh"); err == nil {
		shell = "pwsh"
	} else if _, err := lookPath("powershell.exe"); err == nil {
		shell = "powershell.exe"
	}
	normalized := strings.ReplaceAll(command, "&&", ";")
	return shell, []string{
		"-NoProfile",
		"-NonInteractive",
		"-ExecutionPolicy",
		"Bypass",
		"-Command",
		normalized,
	}
}

func readFile(args map[string]any, cwd string) protocol.ExecToolResult {
	filePath, ok := args["file_path"].(string)
	if !ok || filePath == "" {
		return errorResult("REMOTE_TOOL_ERROR", "file_path must be a non-empty string")
	}
	offset, _ := asInt(args["offset"])
	if offset <= 0 {
		offset = 1
	}
	limit, _ := asInt(args["limit"])
	if limit <= 0 {
		limit = 2000
	}
	override, _ := args["override"].(bool)

	resolved, err := resolvePath(cwd, filePath)
	if err != nil {
		return errorResult("REMOTE_TOOL_ERROR", err.Error())
	}
	data, err := os.ReadFile(resolved)
	if err != nil {
		return errorResult("REMOTE_TOOL_ERROR", err.Error())
	}
	text := strings.ReplaceAll(string(data), "\r\n", "\n")
	lines := strings.Split(text, "\n")
	if len(lines) > 0 && lines[len(lines)-1] == "" {
		lines = lines[:len(lines)-1]
	}
	if override {
		return protocol.ExecToolResult{OK: true, Result: joinNumbered(lines, 0)}
	}
	start := offset - 1
	if start >= len(lines) {
		return protocol.ExecToolResult{OK: true, Result: "(empty file)"}
	}
	end := start + limit
	if end > len(lines) {
		end = len(lines)
	}
	result := joinNumbered(lines[start:end], start)
	if result == "" {
		result = "(empty file)"
	}
	if end < len(lines) {
		result += fmt.Sprintf("\n... (%d lines total, showing %d-%d; use override=true to read full file)", len(lines), start+1, end)
	}
	return protocol.ExecToolResult{OK: true, Result: result}
}

func writeFile(args map[string]any, cwd string, expectedState map[string]any) protocol.ExecToolResult {
	mutation, err := buildFileMutation("write_file", args, cwd)
	if err != nil {
		return errorResult("REMOTE_TOOL_ERROR", err.Error())
	}
	if stale := validateExpectedState(expectedState, mutation.oldState, mutation.resolvedPath); stale != "" {
		return errorResult("REMOTE_TOOL_STALE_PREVIEW", stale)
	}
	if err := os.MkdirAll(filepath.Dir(mutation.resolvedPath), 0o755); err != nil {
		return errorResult("REMOTE_TOOL_ERROR", err.Error())
	}
	if err := os.WriteFile(mutation.resolvedPath, []byte(mutation.newContent), 0o644); err != nil {
		return errorResult("REMOTE_TOOL_ERROR", err.Error())
	}
	return protocol.ExecToolResult{OK: true, Result: fmt.Sprintf("Wrote %d lines to %s", mutation.lineCount, mutation.filePath)}
}

func editFile(args map[string]any, cwd string, expectedState map[string]any) protocol.ExecToolResult {
	mutation, err := buildFileMutation("edit_file", args, cwd)
	if err != nil {
		return errorResult("REMOTE_TOOL_ERROR", err.Error())
	}
	if stale := validateExpectedState(expectedState, mutation.oldState, mutation.resolvedPath); stale != "" {
		return errorResult("REMOTE_TOOL_STALE_PREVIEW", stale)
	}
	if err := os.WriteFile(mutation.resolvedPath, []byte(mutation.newContent), 0o644); err != nil {
		return errorResult("REMOTE_TOOL_ERROR", err.Error())
	}
	return protocol.ExecToolResult{OK: true, Result: fmt.Sprintf("Edited %s", mutation.filePath)}
}

type fileState struct {
	exists  bool
	sha256  string
	size    int64
	mtimeNS int64
}

type fileMutation struct {
	toolName     string
	filePath     string
	resolvedPath string
	oldContent   string
	newContent   string
	oldState     fileState
	diff         string
	lineCount    int
}

func (m fileMutation) diffTitle() string {
	if m.toolName == "edit_file" {
		return "Proposed edit diff"
	}
	return "Proposed file diff"
}

func buildFileMutation(toolName string, args map[string]any, cwd string) (*fileMutation, error) {
	filePath, ok := args["file_path"].(string)
	if !ok || filePath == "" {
		return nil, fmt.Errorf("file_path must be a non-empty string")
	}
	resolved, err := resolvePath(cwd, filePath)
	if err != nil {
		return nil, err
	}
	oldContent, oldState, err := readFileSnapshot(resolved)
	if err != nil {
		return nil, err
	}

	var newContent string
	switch toolName {
	case "write_file":
		content, ok := args["content"].(string)
		if !ok {
			return nil, fmt.Errorf("content must be a string")
		}
		newContent = content
	case "edit_file":
		if !oldState.exists {
			return nil, fmt.Errorf("file does not exist: %s", filePath)
		}
		oldString, ok := args["old_string"].(string)
		if !ok {
			return nil, fmt.Errorf("old_string must be a string")
		}
		newString, ok := args["new_string"].(string)
		if !ok {
			return nil, fmt.Errorf("new_string must be a string")
		}
		if oldString == newString {
			return nil, fmt.Errorf("old_string and new_string must differ")
		}
		count := strings.Count(oldContent, oldString)
		if count == 0 {
			return nil, fmt.Errorf("old_string not found in %s", filePath)
		}
		if count > 1 {
			return nil, fmt.Errorf("old_string appears %d times in %s", count, filePath)
		}
		newContent = strings.Replace(oldContent, oldString, newString, 1)
	default:
		return nil, fmt.Errorf("unsupported tool %q", toolName)
	}

	return &fileMutation{
		toolName:     toolName,
		filePath:     filePath,
		resolvedPath: resolved,
		oldContent:   oldContent,
		newContent:   newContent,
		oldState:     oldState,
		diff:         unifiedWholeFileDiff(oldContent, newContent, filePath),
		lineCount:    countLines(newContent),
	}, nil
}

func readFileSnapshot(path string) (string, fileState, error) {
	stat, err := os.Stat(path)
	if err != nil {
		if os.IsNotExist(err) {
			return "", fileState{exists: false}, nil
		}
		return "", fileState{}, err
	}
	if stat.IsDir() {
		return "", fileState{}, fmt.Errorf("%s is a directory", path)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return "", fileState{}, err
	}
	sum := sha256.Sum256(data)
	return string(data), fileState{
		exists:  true,
		sha256:  fmt.Sprintf("%x", sum),
		size:    stat.Size(),
		mtimeNS: stat.ModTime().UnixNano(),
	}, nil
}

func validateExpectedState(expected map[string]any, current fileState, resolvedPath string) string {
	if len(expected) == 0 {
		return ""
	}
	if expectedPath, _ := expected["resolved_path"].(string); expectedPath != "" && filepath.Clean(expectedPath) != filepath.Clean(resolvedPath) {
		return fmt.Sprintf("approved preview targeted %s, current execution targets %s", expectedPath, resolvedPath)
	}
	if expectedExists, ok := asBool(expected["old_exists"]); ok && expectedExists != current.exists {
		return "file changed since approval preview was generated"
	}
	if expectedSHA, _ := expected["old_sha256"].(string); current.exists && expectedSHA != "" && expectedSHA != current.sha256 {
		return "file content changed since approval preview was generated"
	}
	if expectedSize, ok := asInt64(expected["old_size"]); ok && current.exists && expectedSize != current.size {
		return "file size changed since approval preview was generated"
	}
	if expectedMTime, ok := asInt64(expected["old_mtime_ns"]); ok && current.exists && expectedMTime != current.mtimeNS {
		return "file timestamp changed since approval preview was generated"
	}
	return ""
}

func unifiedWholeFileDiff(oldContent, newContent, filename string) string {
	if oldContent == newContent {
		return ""
	}
	oldLines := splitDiffLines(oldContent)
	newLines := splitDiffLines(newContent)
	var b strings.Builder
	b.WriteString(fmt.Sprintf("--- a/%s\n", filename))
	b.WriteString(fmt.Sprintf("+++ b/%s\n", filename))
	b.WriteString(fmt.Sprintf("@@ -1,%d +1,%d @@\n", len(oldLines), len(newLines)))
	for _, line := range oldLines {
		b.WriteString("-")
		b.WriteString(line)
		b.WriteString("\n")
	}
	for _, line := range newLines {
		b.WriteString("+")
		b.WriteString(line)
		b.WriteString("\n")
	}
	result := b.String()
	if len(result) > 20000 {
		return result[:19000] + "\n... (diff truncated)\n"
	}
	return result
}

func splitDiffLines(content string) []string {
	if content == "" {
		return []string{}
	}
	normalized := strings.ReplaceAll(content, "\r\n", "\n")
	lines := strings.Split(normalized, "\n")
	if len(lines) > 0 && lines[len(lines)-1] == "" {
		lines = lines[:len(lines)-1]
	}
	return lines
}

func previewTexts(oldContent, newContent string) (string, string) {
	if len(oldContent)+len(newContent) > 512000 {
		return "", ""
	}
	return oldContent, newContent
}

func countLines(content string) int {
	if content == "" {
		return 0
	}
	return strings.Count(content, "\n") + 1
}

func globFiles(args map[string]any, cwd string) protocol.ExecToolResult {
	pattern, ok := args["pattern"].(string)
	if !ok || pattern == "" {
		return errorResult("REMOTE_TOOL_ERROR", "pattern must be a non-empty string")
	}
	pathValue, _ := args["path"].(string)
	if pathValue == "" {
		pathValue = "."
	}
	base, err := resolvePath(cwd, pathValue)
	if err != nil {
		return errorResult("REMOTE_TOOL_ERROR", err.Error())
	}
	var matches []string
	walkErr := filepath.WalkDir(base, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if d.IsDir() {
			if _, skip := skipDirs[d.Name()]; skip && path != base {
				return filepath.SkipDir
			}
			return nil
		}
		rel, err := filepath.Rel(base, path)
		if err != nil {
			return nil
		}
		matched, err := filepath.Match(pattern, rel)
		if err == nil && matched {
			matches = append(matches, path)
		}
		if strings.Contains(pattern, "**") {
			alt := strings.ReplaceAll(pattern, "**/", "")
			matchedAlt, err := filepath.Match(alt, filepath.Base(path))
			if err == nil && matchedAlt {
				matches = append(matches, path)
			}
		}
		return nil
	})
	if walkErr != nil {
		return errorResult("REMOTE_TOOL_ERROR", walkErr.Error())
	}
	sort.Strings(matches)
	if len(matches) == 0 {
		return protocol.ExecToolResult{OK: true, Result: "No files matched."}
	}
	if len(matches) > 100 {
		matches = append(matches[:100], fmt.Sprintf("... (%d matches, showing first 100)", len(matches)))
	}
	return protocol.ExecToolResult{OK: true, Result: strings.Join(dedupe(matches), "\n")}
}

func grepFiles(args map[string]any, cwd string) protocol.ExecToolResult {
	pattern, ok := args["pattern"].(string)
	if !ok || pattern == "" {
		return errorResult("REMOTE_TOOL_ERROR", "pattern must be a non-empty string")
	}
	pathValue, _ := args["path"].(string)
	if pathValue == "" {
		pathValue = "."
	}
	include, _ := args["include"].(string)
	base, err := resolvePath(cwd, pathValue)
	if err != nil {
		return errorResult("REMOTE_TOOL_ERROR", err.Error())
	}
	re, err := regexp.Compile(pattern)
	if err != nil {
		return errorResult("REMOTE_TOOL_ERROR", fmt.Sprintf("Invalid regex: %v", err))
	}
	var files []string
	stat, err := os.Stat(base)
	if err != nil {
		return errorResult("REMOTE_TOOL_ERROR", err.Error())
	}
	if stat.IsDir() {
		_ = filepath.WalkDir(base, func(path string, d os.DirEntry, err error) error {
			if err != nil {
				return nil
			}
			if d.IsDir() {
				if _, skip := skipDirs[d.Name()]; skip && path != base {
					return filepath.SkipDir
				}
				return nil
			}
			if include != "" {
				matched, matchErr := filepath.Match(include, filepath.Base(path))
				if matchErr != nil || !matched {
					return nil
				}
			}
			files = append(files, path)
			return nil
		})
	} else {
		files = append(files, base)
	}
	var matches []string
	for _, file := range files {
		data, err := os.ReadFile(file)
		if err != nil {
			continue
		}
		for idx, line := range strings.Split(strings.ReplaceAll(string(data), "\r\n", "\n"), "\n") {
			if re.MatchString(line) {
				matches = append(matches, fmt.Sprintf("%s:%d: %s", file, idx+1, line))
				if len(matches) >= 200 {
					matches = append(matches, "... (200 match limit reached)")
					return protocol.ExecToolResult{OK: true, Result: strings.Join(matches, "\n")}
				}
			}
		}
	}
	if len(matches) == 0 {
		return protocol.ExecToolResult{OK: true, Result: "No matches found."}
	}
	return protocol.ExecToolResult{OK: true, Result: strings.Join(matches, "\n")}
}

func resolvePath(cwd, path string) (string, error) {
	if filepath.IsAbs(path) {
		return filepath.Clean(path), nil
	}
	if cwd == "" {
		return filepath.Abs(path)
	}
	return filepath.Abs(filepath.Join(cwd, path))
}

func joinNumbered(lines []string, start int) string {
	if len(lines) == 0 {
		return "(empty file)"
	}
	parts := make([]string, 0, len(lines))
	for i, line := range lines {
		parts = append(parts, fmt.Sprintf("%d\t%s", start+i+1, line))
	}
	return strings.Join(parts, "\n")
}

func errorResult(code, message string) protocol.ExecToolResult {
	return protocol.ExecToolResult{OK: false, ErrorCode: code, ErrorMessage: message}
}

func asInt(v any) (int, bool) {
	switch n := v.(type) {
	case int:
		return n, true
	case int32:
		return int(n), true
	case int64:
		return int(n), true
	case float64:
		return int(n), true
	default:
		return 0, false
	}
}

func asInt64(v any) (int64, bool) {
	switch n := v.(type) {
	case int:
		return int64(n), true
	case int32:
		return int64(n), true
	case int64:
		return n, true
	case float64:
		return int64(n), true
	default:
		return 0, false
	}
}

func asBool(v any) (bool, bool) {
	b, ok := v.(bool)
	return b, ok
}

func dedupe(items []string) []string {
	seen := map[string]struct{}{}
	out := make([]string, 0, len(items))
	for _, item := range items {
		if _, ok := seen[item]; ok {
			continue
		}
		seen[item] = struct{}{}
		out = append(out, item)
	}
	return out
}

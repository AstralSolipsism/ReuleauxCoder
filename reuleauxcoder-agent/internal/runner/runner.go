package runner

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/RC-CHN/ReuleauxCoder/reuleauxcoder-agent/internal/client"
	"github.com/RC-CHN/ReuleauxCoder/reuleauxcoder-agent/internal/mcp"
	"github.com/RC-CHN/ReuleauxCoder/reuleauxcoder-agent/internal/protocol"
	"github.com/RC-CHN/ReuleauxCoder/reuleauxcoder-agent/internal/tools"
	"github.com/charmbracelet/glamour"
)

type Config struct {
	Host           string
	BootstrapToken string
	CWD            string
	WorkspaceRoot  string
	PeerInfoFile   string
	PollInterval   time.Duration
	Interactive    bool
	EnvSync        bool
}

type Runner struct {
	cfg      Config
	client   *client.HTTPClient
	scanner  *bufio.Scanner
	mdRender *glamour.TermRenderer
	mcp      *mcp.Supervisor
	activeMu sync.Mutex
	active   map[string]context.CancelFunc
}

func New(cfg Config) *Runner {
	renderer, err := glamour.NewTermRenderer(
		glamour.WithAutoStyle(),
		glamour.WithWordWrap(100),
	)
	if err != nil {
		log.Printf("markdown renderer init failed: %v", err)
	}
	return &Runner{
		cfg:      cfg,
		client:   client.New(cfg.Host),
		scanner:  bufio.NewScanner(os.Stdin),
		mdRender: renderer,
		active:   map[string]context.CancelFunc{},
	}
}

func (r *Runner) Run(ctx context.Context) error {
	cwd := r.cfg.CWD
	if cwd == "" {
		resolved, err := os.Getwd()
		if err != nil {
			return err
		}
		cwd = resolved
	}
	workspaceRoot := r.cfg.WorkspaceRoot
	if workspaceRoot == "" {
		workspaceRoot = cwd
	}

	registerResp, err := r.client.Register(ctx, protocol.RegisterRequest{
		BootstrapToken: r.cfg.BootstrapToken,
		CWD:            cwd,
		WorkspaceRoot:  workspaceRoot,
		Capabilities:   []string{"shell", "read_file", "write_file", "edit_file", "glob", "grep", "tool_preview"},
		HostInfoMin: map[string]any{
			"os":       runtimeOS(),
			"arch":     runtimeArch(),
			"hostname": runtimeHostname(),
		},
	})
	if err != nil {
		return fmt.Errorf("register failed: %w", err)
	}
	if r.cfg.PeerInfoFile != "" {
		if err := writePeerInfoFile(r.cfg.PeerInfoFile, registerResp); err != nil {
			return fmt.Errorf("write peer info failed: %w", err)
		}
	}
	log.Printf("registered peer_id=%s", registerResp.PeerID)
	fmt.Printf("\n=== REMOTE PEER CONNECTED ===\nPeer: %s\nWorkspace: %s\nHost: %s\n============================\n\n", registerResp.PeerID, workspaceRoot, r.cfg.Host)

	heartbeatInterval := time.Duration(registerResp.HeartbeatIntervalSec) * time.Second
	if heartbeatInterval <= 0 {
		heartbeatInterval = 10 * time.Second
	}
	pollInterval := r.cfg.PollInterval
	if pollInterval <= 0 {
		pollInterval = 500 * time.Millisecond
	}

	childCtx, cancel := signal.NotifyContext(ctx, os.Interrupt, syscall.SIGTERM)
	defer cancel()
	defer func() {
		if r.mcp != nil {
			r.mcp.Stop()
		}
		disconnectCtx, cancelDisconnect := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancelDisconnect()
		_ = r.client.Disconnect(disconnectCtx, protocol.DisconnectRequest{
			PeerToken: registerResp.PeerToken,
			Reason:    "peer_shutdown",
		})
	}()

	go r.heartbeatLoop(childCtx, registerResp.PeerToken, heartbeatInterval)
	if r.cfg.EnvSync {
		errCh := make(chan error, 1)
		go func() {
			errCh <- r.runPollLoop(childCtx, registerResp.PeerToken, cwd, pollInterval)
		}()
		err := r.runEnvironmentSync(childCtx, registerResp.PeerToken, workspaceRoot)
		cancel()
		select {
		case pollErr := <-errCh:
			if err == nil && pollErr != nil && childCtx.Err() == nil {
				return pollErr
			}
		default:
		}
		return err
	}

	r.mcp = mcp.NewSupervisor(r.client, registerResp.PeerToken, workspaceRoot)
	r.mcp.Start(childCtx)

	if r.cfg.Interactive {
		errCh := make(chan error, 1)
		go func() {
			errCh <- r.runPollLoop(childCtx, registerResp.PeerToken, cwd, pollInterval)
		}()

		if err := r.runInteractiveLoop(childCtx, registerResp.PeerToken); err != nil {
			return err
		}
		cancel()
		select {
		case err := <-errCh:
			if err != nil && childCtx.Err() == nil {
				return err
			}
		default:
		}
		return nil
	}

	return r.runPollLoop(childCtx, registerResp.PeerToken, cwd, pollInterval)
}

func (r *Runner) runEnvironmentSync(ctx context.Context, peerToken, workspaceRoot string) error {
	manifestCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
	resp, err := r.client.EnvironmentManifest(manifestCtx, protocol.EnvironmentManifestRequest{
		PeerToken: peerToken,
		OS:        runtimeOS(),
		Arch:      runtimeArch(),
		Workspace: workspaceRoot,
	})
	cancel()
	if err != nil {
		return fmt.Errorf("environment manifest failed: %w", err)
	}
	entryCount := len(resp.CLITools) + len(resp.MCPServers)
	if entryCount == 0 || strings.TrimSpace(resp.Prompt) == "" {
		fmt.Println("No environment entries are configured on the host.")
		return nil
	}
	fmt.Printf("Starting environment sync agent for %d environment entry(s).\n", entryCount)
	return r.runRemoteChat(ctx, peerToken, resp.Prompt)
}

func writePeerInfoFile(path string, resp protocol.RegisterResponse) error {
	payload, err := json.MarshalIndent(map[string]any{
		"peer_id":                resp.PeerID,
		"peer_token":             resp.PeerToken,
		"heartbeat_interval_sec": resp.HeartbeatIntervalSec,
	}, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, payload, 0o600)
}

func (r *Runner) runPollLoop(ctx context.Context, peerToken, cwd string, pollInterval time.Duration) error {
	for {
		select {
		case <-ctx.Done():
			return nil
		default:
		}

		pollCtx, cancelPoll := context.WithTimeout(ctx, 30*time.Second)
		env, err := r.client.Poll(pollCtx, protocol.PollRequest{PeerToken: peerToken})
		cancelPoll()
		if err != nil {
			return fmt.Errorf("poll failed: %w", err)
		}

		switch env.Type {
		case "noop", "":
			time.Sleep(pollInterval)
			continue
		case "exec_tool":
			execReq, err := protocol.DecodeExecToolRequest(env.Payload)
			if err != nil {
				if sendErr := r.sendToolResult(ctx, peerToken, env.RequestID, protocol.ExecToolResult{
					OK:           false,
					ErrorCode:    "REMOTE_TOOL_ERROR",
					ErrorMessage: err.Error(),
				}); sendErr != nil {
					return sendErr
				}
				continue
			}
			execCtx, cancelExec := context.WithCancel(ctx)
			r.registerActiveRequest(env.RequestID, cancelExec)
			go func(requestID string, execReq protocol.ExecToolRequest) {
				defer r.unregisterActiveRequest(requestID)
				var result protocol.ExecToolResult
				if execReq.ToolName == "mcp" {
					if r.mcp == nil {
						result = protocol.ExecToolResult{OK: false, ErrorCode: "REMOTE_MCP_ERROR", ErrorMessage: "MCP supervisor is not running"}
					} else {
						result = r.mcp.Execute(execReq.Args)
					}
				} else {
					result = tools.ExecuteWithContext(execCtx, execReq, cwd, func(chunk protocol.ToolStreamChunk) {
						if chunk.Meta == nil {
							chunk.Meta = map[string]any{}
						}
						if execReq.ToolCallID != "" {
							chunk.Meta["tool_call_id"] = execReq.ToolCallID
						}
						if sendErr := r.sendToolStream(context.Background(), peerToken, requestID, chunk); sendErr != nil {
							log.Printf("stream send failed: %v", sendErr)
						}
					})
				}
				if result.Meta == nil {
					result.Meta = map[string]any{}
				}
				if execReq.ToolCallID != "" {
					result.Meta["tool_call_id"] = execReq.ToolCallID
				}
				if sendErr := r.sendToolResult(context.Background(), peerToken, requestID, result); sendErr != nil {
					log.Printf("tool result send failed: %v", sendErr)
				}
			}(env.RequestID, execReq)
		case "cancel_tool":
			requestID := env.RequestID
			if requestID == "" {
				if payloadRequestID, _ := env.Payload["request_id"].(string); payloadRequestID != "" {
					requestID = payloadRequestID
				}
			}
			if requestID != "" {
				r.cancelActiveRequest(requestID)
			}
		case "preview_tool":
			previewReq, err := protocol.DecodeToolPreviewRequest(env.Payload)
			if err != nil {
				if sendErr := r.sendToolPreviewResult(ctx, peerToken, env.RequestID, protocol.ToolPreviewResult{
					OK:           false,
					ErrorCode:    "REMOTE_TOOL_ERROR",
					ErrorMessage: err.Error(),
				}); sendErr != nil {
					return sendErr
				}
				continue
			}
			result := tools.Preview(previewReq, cwd)
			if sendErr := r.sendToolPreviewResult(ctx, peerToken, env.RequestID, result); sendErr != nil {
				return sendErr
			}
		case "cleanup":
			cleanup := protocol.CleanupResult{OK: true, RemovedItems: []string{}}
			if err := r.sendCleanupResult(ctx, peerToken, env.RequestID, cleanup); err != nil {
				return err
			}
		default:
			log.Printf("ignoring unsupported envelope type=%s", env.Type)
			time.Sleep(pollInterval)
		}
	}
}

func (r *Runner) registerActiveRequest(requestID string, cancel context.CancelFunc) {
	if requestID == "" {
		return
	}
	r.activeMu.Lock()
	r.active[requestID] = cancel
	r.activeMu.Unlock()
}

func (r *Runner) unregisterActiveRequest(requestID string) {
	if requestID == "" {
		return
	}
	r.activeMu.Lock()
	delete(r.active, requestID)
	r.activeMu.Unlock()
}

func (r *Runner) cancelActiveRequest(requestID string) {
	r.activeMu.Lock()
	cancel := r.active[requestID]
	r.activeMu.Unlock()
	if cancel != nil {
		cancel()
	}
}

func (r *Runner) runInteractiveLoop(ctx context.Context, peerToken string) error {
	for {
		select {
		case <-ctx.Done():
			return nil
		default:
		}

		fmt.Print("You > ")
		if !r.scanner.Scan() {
			if err := r.scanner.Err(); err != nil {
				return err
			}
			return nil
		}
		userInput := strings.TrimSpace(r.scanner.Text())
		if userInput == "" {
			continue
		}
		if userInput == "/quit" || userInput == "/exit" {
			return nil
		}
		if err := r.runRemoteChat(ctx, peerToken, userInput); err != nil {
			return err
		}
	}
}

func (r *Runner) runRemoteChat(ctx context.Context, peerToken, prompt string) error {
	chatCtx, cancel := context.WithTimeout(ctx, 10*time.Minute)
	startResp, err := r.client.ChatStart(chatCtx, protocol.ChatStartRequest{
		PeerToken: peerToken,
		Prompt:    prompt,
	})
	cancel()
	if err != nil {
		return fmt.Errorf("chat start failed: %w", err)
	}
	if strings.TrimSpace(startResp.Error) != "" {
		return fmt.Errorf("chat start failed: %s", startResp.Error)
	}
	if strings.TrimSpace(startResp.ChatID) == "" {
		return fmt.Errorf("chat start failed: empty chat id")
	}

	cursor := 0
	for {
		streamCtx, cancel := context.WithTimeout(ctx, 35*time.Second)
		streamResp, err := r.client.ChatStream(streamCtx, protocol.ChatStreamRequest{
			PeerToken:  peerToken,
			ChatID:     startResp.ChatID,
			Cursor:     cursor,
			TimeoutSec: 30,
		})
		cancel()
		if err != nil {
			return fmt.Errorf("chat stream failed: %w", err)
		}
		if strings.TrimSpace(streamResp.Error) != "" {
			return fmt.Errorf("chat stream failed: %s", streamResp.Error)
		}
		for _, event := range streamResp.Events {
			if err := r.handleChatEvent(ctx, peerToken, startResp.ChatID, event); err != nil {
				return err
			}
		}
		cursor = streamResp.NextCursor
		if streamResp.Done {
			return nil
		}
	}
}

func (r *Runner) handleChatEvent(ctx context.Context, peerToken, chatID string, event protocol.ChatEvent) error {
	switch event.Type {
	case "chat_start":
		return nil
	case "output":
		r.renderOutputEvent(event.Payload)
	case "tool_call_stream":
		r.renderToolStream(event.Payload)
	case "approval_request":
		return r.handleApprovalRequest(ctx, peerToken, chatID, event.Payload)
	case "approval_resolved":
		return nil
	case "tool_call_start":
		if name, _ := event.Payload["tool_name"].(string); name != "" {
			fmt.Printf("\n[tool] %s\n", name)
		}
	case "tool_call_end":
		return nil
	case "chat_end":
		if response, _ := event.Payload["response"].(string); strings.TrimSpace(response) != "" {
			fmt.Println()
		}
	case "error":
		msg, _ := event.Payload["message"].(string)
		if msg == "" {
			msg = "unknown error"
		}
		fmt.Fprintf(os.Stderr, "\nError: %s\n", msg)
	}
	return nil
}

func (r *Runner) handleApprovalRequest(ctx context.Context, peerToken, chatID string, payload map[string]any) error {
	r.renderOutputEvent(payload)

	approvalID, _ := payload["approval_id"].(string)
	if approvalID == "" {
		return fmt.Errorf("approval request missing approval_id")
	}

	fmt.Print("Approve? [y/N]: ")
	decision := "deny_once"
	if r.scanner.Scan() {
		answer := strings.ToLower(strings.TrimSpace(r.scanner.Text()))
		if answer == "y" || answer == "yes" || answer == "a" || answer == "allow" {
			decision = "allow_once"
		}
	} else if err := r.scanner.Err(); err != nil {
		return err
	}

	replyCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	replyResp, err := r.client.ApprovalReply(replyCtx, protocol.ApprovalReplyRequest{
		PeerToken:  peerToken,
		ChatID:     chatID,
		ApprovalID: approvalID,
		Decision:   decision,
	})
	if err != nil {
		return fmt.Errorf("approval reply failed: %w", err)
	}
	if !replyResp.OK {
		return fmt.Errorf("approval reply failed: %s", replyResp.Error)
	}
	return nil
}

func (r *Runner) renderOutputEvent(payload map[string]any) {
	format, _ := payload["format"].(string)
	content, _ := payload["content"].(string)
	if content == "" {
		return
	}

	switch format {
	case "markdown":
		if r.mdRender != nil {
			rendered, err := r.mdRender.Render(content)
			if err == nil {
				fmt.Print(rendered)
				return
			}
		}
		fmt.Print(content)
	case "plain", "terminal", "":
		fmt.Print(content)
	default:
		fmt.Print(content)
	}

	if newline, ok := payload["newline"].(bool); ok && newline {
		fmt.Print("\n")
	}
}

func (r *Runner) renderToolStream(payload map[string]any) {
	content, _ := payload["content"].(string)
	if content == "" {
		return
	}
	stream, _ := payload["stream"].(string)
	if stream == "stderr" {
		fmt.Fprint(os.Stderr, content)
		return
	}
	fmt.Print(content)
}

func (r *Runner) heartbeatLoop(ctx context.Context, peerToken string, interval time.Duration) {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			hbCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
			err := r.client.Heartbeat(hbCtx, protocol.Heartbeat{
				PeerToken: peerToken,
				TS:        float64(time.Now().UnixNano()) / 1e9,
			})
			cancel()
			if err != nil {
				log.Printf("heartbeat failed: %v", err)
			}
		}
	}
}

func (r *Runner) sendToolResult(ctx context.Context, peerToken, requestID string, result protocol.ExecToolResult) error {
	sendCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	return r.client.SendResult(sendCtx, protocol.ResultRequest{
		PeerToken: peerToken,
		RequestID: requestID,
		Type:      "tool_result",
		Payload:   mapFromStruct(result),
	})
}

func (r *Runner) sendToolStream(ctx context.Context, peerToken, requestID string, chunk protocol.ToolStreamChunk) error {
	sendCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	return r.client.SendResult(sendCtx, protocol.ResultRequest{
		PeerToken: peerToken,
		RequestID: requestID,
		Type:      "tool_stream",
		Payload:   mapFromStruct(chunk),
	})
}

func (r *Runner) sendToolPreviewResult(ctx context.Context, peerToken, requestID string, result protocol.ToolPreviewResult) error {
	sendCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	return r.client.SendResult(sendCtx, protocol.ResultRequest{
		PeerToken: peerToken,
		RequestID: requestID,
		Type:      "tool_preview_result",
		Payload:   mapFromStruct(result),
	})
}

func (r *Runner) sendCleanupResult(ctx context.Context, peerToken, requestID string, result protocol.CleanupResult) error {
	sendCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	return r.client.SendResult(sendCtx, protocol.ResultRequest{
		PeerToken: peerToken,
		RequestID: requestID,
		Type:      "cleanup_result",
		Payload:   mapFromStruct(result),
	})
}

// mapFromStruct converts a small control-plane struct to map[string]any via JSON roundtrip.
// This keeps field tags and omitempty behavior consistent with the wire protocol.
func mapFromStruct(v any) map[string]any {
	buf, err := json.Marshal(v)
	if err != nil {
		return map[string]any{}
	}
	out := map[string]any{}
	if err := json.Unmarshal(buf, &out); err != nil {
		return map[string]any{}
	}
	return out
}

func runtimeOS() string {
	return runtime.GOOS
}

func runtimeArch() string {
	return runtime.GOARCH
}

func runtimeHostname() string {
	hostname, err := os.Hostname()
	if err != nil {
		return ""
	}
	return hostname
}

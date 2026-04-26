package main

import (
	"context"
	"flag"
	"log"
	"os"
	"time"

	"github.com/RC-CHN/ReuleauxCoder/reuleauxcoder-agent/internal/runner"
)

func main() {
	var (
		host           string
		bootstrapToken string
		cwd            string
		workspaceRoot  string
		peerInfoFile   string
		pollInterval   time.Duration
		interactive    bool
		envSync        bool
	)

	flag.StringVar(&host, "host", "", "Remote relay host base URL")
	flag.StringVar(&bootstrapToken, "bootstrap-token", "", "One-time bootstrap token")
	flag.StringVar(&cwd, "cwd", "", "Working directory for remote tool execution")
	flag.StringVar(&workspaceRoot, "workspace-root", "", "Workspace root reported to host")
	flag.StringVar(&peerInfoFile, "peer-info-file", "", "Write peer registration info to this JSON file")
	flag.DurationVar(&pollInterval, "poll-interval", 500*time.Millisecond, "Polling interval when no work is available")
	flag.BoolVar(&interactive, "interactive", false, "Run interactive chat loop proxied through host")
	flag.BoolVar(&envSync, "env-sync", false, "Run one lightweight CLI environment sync chat and exit")
	flag.Parse()

	if host == "" {
		log.Print("missing required --host")
		os.Exit(2)
	}
	if bootstrapToken == "" {
		log.Print("missing required --bootstrap-token")
		os.Exit(2)
	}

	r := runner.New(runner.Config{
		Host:           host,
		BootstrapToken: bootstrapToken,
		CWD:            cwd,
		WorkspaceRoot:  workspaceRoot,
		PeerInfoFile:   peerInfoFile,
		PollInterval:   pollInterval,
		Interactive:    interactive,
		EnvSync:        envSync,
	})
	if err := r.Run(context.Background()); err != nil {
		log.Printf("agent exited with error: %v", err)
		os.Exit(1)
	}
}

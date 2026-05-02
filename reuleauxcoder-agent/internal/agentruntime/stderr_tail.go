package agentruntime

import (
	"io"
	"strings"
	"sync"
)

const agentStderrTailBytes = 2048

type stderrTail struct {
	inner io.Writer
	max   int
	mu    sync.Mutex
	buf   []byte
}

func newStderrTail(inner io.Writer, max int) *stderrTail {
	if max <= 0 {
		max = agentStderrTailBytes
	}
	return &stderrTail{inner: inner, max: max}
}

func (s *stderrTail) Write(p []byte) (int, error) {
	if s.inner != nil {
		if _, err := s.inner.Write(p); err != nil {
			return 0, err
		}
	}
	s.mu.Lock()
	s.buf = append(s.buf, p...)
	if len(s.buf) > s.max {
		s.buf = s.buf[len(s.buf)-s.max:]
	}
	s.mu.Unlock()
	return len(p), nil
}

func (s *stderrTail) Tail() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return strings.TrimSpace(string(s.buf))
}

func withAgentStderr(message, label, tail string) string {
	tail = strings.TrimSpace(tail)
	if tail == "" {
		return message
	}
	return message + "; " + label + " stderr: " + tail
}

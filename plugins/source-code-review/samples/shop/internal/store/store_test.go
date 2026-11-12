package store

import "testing"

func TestPing(t *testing.T) {
	if Ping() != "ok" {
		t.Fatal("ping")
	}
}

package messaging

import (
	"testing"

	"gotest.tools/v3/assert"
)

func TestMatches(t *testing.T) {
	cases := []struct {
		name    string
		pattern string
		topic   string
		want    bool
	}{
		{"exact match", "nodes/n1/control/deploy", "nodes/n1/control/deploy", true},
		{"plus wildcard matches one segment", "nodes/+/information", "nodes/n1/information", true},
		{"plus does not swallow an extra segment", "nodes/+/information", "nodes/n1/extra/information", false},
		{"plus does not swallow a longer trailing segment", "nodes/+/net/subnet", "nodes/n1/net/subnetwork/result", false},
		{"foreign prefix does not match", "nodes/n1/control/deploy", "xnodes/n1/control/deploy", false},
		{"trailing extra segment does not match without hash", "nodes/n1/control/deploy", "nodes/n1/control/deploy/extra", false},
		{"unrelated prefix does not match", "nodes/n1/control/deploy", "other/n1/control/deploy", false},
		{"hash matches the exact prefix with zero remaining segments", "nodes/n1/#", "nodes/n1", true},
		{"hash matches multiple remaining segments", "nodes/n1/#", "nodes/n1/a/b", true},
		{"hash matches a single remaining segment", "nodes/n1/#", "nodes/n1/a", true},
		{"hash requires the prefix to match", "nodes/n1/#", "nodes/n2/a", false},
		{"hash not at the end never matches", "nodes/#/control", "nodes/n1/control", false},
		{"hash not at the end never matches even the literal topic", "nodes/#/control", "nodes/#/control", false},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			assert.Equal(t, Matches(tc.pattern, tc.topic), tc.want)
		})
	}
}

package messaging

import "strings"

// Matches reports whether topic satisfies pattern, using the "/"-separated
// wildcard syntax shared by every Oakestra bus adapter: "+" matches exactly
// one segment, "#" matches the rest of the topic (including zero segments)
// and is only meaningful as the last segment of pattern. A "#" anywhere else
// makes the pattern invalid, so it never matches anything.
func Matches(pattern, topic string) bool {
	patternSegs := strings.Split(pattern, "/")
	topicSegs := strings.Split(topic, "/")

	for i, seg := range patternSegs {
		if seg == "#" {
			if i != len(patternSegs)-1 {
				return false
			}
			return i <= len(topicSegs)
		}
		if i >= len(topicSegs) {
			return false
		}
		if seg == "+" {
			continue
		}
		if seg != topicSegs[i] {
			return false
		}
	}
	return len(patternSegs) == len(topicSegs)
}

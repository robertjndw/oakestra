def matches(pattern: str, topic: str) -> bool:
    """Match a canonical "/"-separated topic against a pattern.

    "+" matches exactly one segment. "#" matches the rest of the topic,
    including zero remaining segments, and is only meaningful as the last
    segment of the pattern.
    """
    pattern_segments = pattern.split("/")
    topic_segments = topic.split("/")

    for index, segment in enumerate(pattern_segments):
        if segment == "#":
            return True
        if index >= len(topic_segments):
            return False
        if segment != "+" and segment != topic_segments[index]:
            return False

    return len(pattern_segments) == len(topic_segments)

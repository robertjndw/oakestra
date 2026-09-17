import pytest
from oakestra_messaging import matches

CASES = [
    # exact match
    ("nodes/n1/information", "nodes/n1/information", True),
    ("nodes/n1/information", "nodes/n1/job", False),
    # "+" matches exactly one segment
    ("nodes/+/information", "nodes/n1/information", True),
    ("nodes/+/information", "nodes/n1/extra/information", False),
    ("nodes/+/net/subnet", "nodes/n1/net/subnet", True),
    ("nodes/+/net/subnet", "nodes/n1/net/subnetwork/result", False),
    ("nodes/+/job", "nodes/n1/job", True),
    ("nodes/+/job", "job", False),
    # "#" matches the rest, including zero remaining segments
    ("nodes/n1/#", "nodes/n1", True),
    ("nodes/n1/#", "nodes/n1/net/subnet", True),
    ("nodes/n1/#", "nodes/n1/net/tablequery/result", True),
    ("#", "anything/at/all", True),
    ("#", "", True),
    # multiple wildcards; "+" is exactly one segment, so a deeper suffix
    # such as "tablequery/request" (two segments) does not match
    ("nodes/+/net/+", "nodes/n1/net/subnet", True),
    ("nodes/+/net/+", "nodes/n1/net/tablequery/request", False),
    ("nodes/+/net/+", "nodes/n1/other/subnet", False),
    # foreign prefixes never match
    ("nodes/+/information", "jobs/j1/updates_available", False),
    ("jobs/+/updates_available", "nodes/n1/information", False),
    ("nodes/+/information", "other/n1/information", False),
    # pattern longer or shorter than topic (no "#")
    ("nodes/+/information", "nodes/n1", False),
    ("nodes/n1", "nodes/n1/information", False),
]


@pytest.mark.parametrize("pattern, topic, expected", CASES)
def test_matches(pattern, topic, expected):
    assert matches(pattern, topic) is expected

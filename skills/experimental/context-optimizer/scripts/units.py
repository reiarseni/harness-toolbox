#!/usr/bin/env python3
"""One unit for every token figure this skill reports: thousands.

A run mixes figures three orders of magnitude apart — a 314-token agent block
against a 287k session — and read side by side in raw digits they do not
compare at a glance. Every token figure is therefore rendered in thousands, so
a number can be weighed against the one above it without counting digits.

The raw integer stays in `value`, because the arithmetic downstream is done on
it; the rendering is added alongside as `display`, and that is the field a
report quotes.
"""

from __future__ import annotations

import copy

# Keys whose `value` is not a token count and must never be rendered in
# thousands: a share of 4.0% read as "0.004k" would be nonsense.
NON_TOKEN_KEYS = ("percent", "share", "deviation", "ratio", "count", "days",
                  "bytes", "usage", "uses", "invocation")

# The key is not always enough. `plugin_usage` carries a `value` and a `basis`
# like any cost block, and on a real run it rendered 101,215 plugin
# invocations as "101.2k" — sitting in a report whose every other k is tokens.
# A block's `method` says where its number came from, so read that too: a
# counter is never a token figure, whatever its key is called.
NON_TOKEN_METHODS = ("counter", "count of", "invocation", "uses", "startups")


def thousands(value: int | float | None) -> str | None:
    """Render a token count in thousands: 29667 -> '29.7k', 653 -> '0.65k'."""
    if value is None:
        return None
    scaled = value / 1000
    # Below a thousand the first decimal is all zero, so a 653-token entry and
    # a 51-token one would both read "0.7k" and "0.1k" — two digits keep them
    # apart. Above it, one decimal is already finer than the client's own
    # figures, which move by hundreds between readings.
    text = f"{scaled:.2f}" if abs(scaled) < 1 else f"{scaled:.1f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text or '0'}k"


def is_token_block(key: str | None, node: dict) -> bool:
    """Whether a block carrying `value` is measuring tokens at all."""
    lowered = (key or "").lower()
    if any(word in lowered for word in NON_TOKEN_KEYS):
        return False
    method = str(node.get("method", "")).lower()
    return not any(word in method for word in NON_TOKEN_METHODS)


def annotate(node, key: str | None = None):
    """Walk a payload and add `display` beside every token figure.

    Recognises the two shapes these scripts emit: a cost block carrying
    `value` and `basis`, and a bare integer under a key that names tokens.
    Anything else is left exactly as it was.
    """
    if isinstance(node, dict):
        for child_key, child in list(node.items()):
            annotate(child, child_key)
        value = node.get("value")
        if (isinstance(value, (int, float)) and not isinstance(value, bool)
                and "basis" in node and "display" not in node
                and is_token_block(key, node)):
            node["display"] = thousands(value)
        for child_key, child in list(node.items()):
            if (isinstance(child, (int, float)) and not isinstance(child, bool)
                    and ("token" in child_key.lower() or child_key.lower().endswith("_cost"))
                    and f"{child_key}_display" not in node):
                node[f"{child_key}_display"] = thousands(child)
    elif isinstance(node, list):
        for item in node:
            annotate(item, key)
    return node


def self_test() -> int:
    failures = []

    for value, expected in ((29667, "29.7k"), (653, "0.65k"), (0, "0k"),
                            (12000, "12k"), (51, "0.05k"), (1_234_567, "1234.6k"),
                            (None, None)):
        got = thousands(value)
        if got != expected:
            failures.append(f"thousands({value}) = {got!r}, expected {expected!r}")

    # A cost block gains a rendering; a percentage must not.
    payload = {
        "totals": {"startup_prompt_cost": {"value": 29667, "basis": "measured"}},
        "entries": [
            {"name": "a", "prompt_cost": {"value": 653, "basis": "estimated"}},
            {"name": "b", "share_percent": {"value": 4.0, "basis": "measured"}},
            {"name": "c", "prompt_cost": {"value": None, "basis": "unavailable"}},
            {"name": "d", "plugin_usage": {"value": 101215, "basis": "measured",
                                           "method": "client pluginUsage counter"}},
        ],
        "saved_tokens": 1340,
        "parsed_entry_count": 68,
    }
    annotate(payload)
    if payload["totals"]["startup_prompt_cost"].get("display") != "29.7k":
        failures.append("a measured total was not rendered in thousands")
    if payload["entries"][0]["prompt_cost"].get("display") != "0.65k":
        failures.append("a sub-thousand entry cost was not rendered")
    if "display" in payload["entries"][1]["share_percent"]:
        failures.append("a percentage was rendered as a token figure")
    if payload["entries"][2]["prompt_cost"].get("display") is not None:
        failures.append("an unavailable cost was given a rendering")
    if "display" in payload["entries"][3]["plugin_usage"]:
        failures.append("an invocation counter was rendered as a token figure")
    if payload.get("saved_tokens_display") != "1.3k":
        failures.append("a bare token integer was not rendered")
    if "parsed_entry_count_display" in payload:
        failures.append("an entry count was rendered as a token figure")

    # Idempotent: a payload annotated twice must not change. The copy has to
    # be deep — a shallow one shares every nested block and would compare equal
    # no matter what the second pass did.
    once = copy.deepcopy(payload)
    annotate(payload)
    if payload != once:
        failures.append("annotating twice changed the payload")

    for line in failures:
        print(f"FAIL {line}")
    if failures:
        return 1
    print("self-test: all checks passed")
    print(f"  29667 -> {thousands(29667)}   653 -> {thousands(653)}   0 -> {thousands(0)}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(self_test())

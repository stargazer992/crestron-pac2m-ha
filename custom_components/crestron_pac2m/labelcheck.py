"""Compares the labels the processor reports with the labels the mapping table expects. No Home Assistant imports.

The processor dumps a name for every join when the session starts. If the name of a join differs from the
name the table was built with, the join -> entity mapping is probably wrong (a light would switch the wrong lamp).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LabelReport:
    """Result of one comparison."""

    mismatched: list[tuple[int, str, str, str]] = field(default_factory=list)   # (join, entity_id, expected, actual)
    missing: list[tuple[int, str, str]] = field(default_factory=list)           # (join, entity_id, expected): no label received
    checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.mismatched

    def summary(self) -> str:
        if not self.checked:
            return "No labels to compare."
        lines = [f"{self.checked - len(self.mismatched) - len(self.missing)} of {self.checked} labels match."]
        for join, entity_id, expected, actual in self.mismatched:
            lines.append(f"MISMATCH join 0x{join:04X} ({entity_id}): table expects '{expected}', processor says '{actual}'")
        for join, entity_id, expected in self.missing:
            lines.append(f"no label received for join 0x{join:04X} ({entity_id}, expected '{expected}')")
        return "\n".join(lines)


def _norm(text: str) -> str:
    return " ".join(text.replace("_", " ").casefold().split())


def compare(lights, labels: dict[int, str], room_for_join=lambda join: None) -> LabelReport:
    """lights = const.LIGHTS rows (join, entity_id, kind, room, label).

    labels = join -> light label from the processor; room_for_join(join) -> room name from the processor (or None).
    Both the light label and the room must match; the room check confirms the whole join -> room grouping.
    """
    report = LabelReport()
    for join, entity_id, _kind, room, expected in lights:
        report.checked += 1
        actual = labels.get(join)
        actual_room = room_for_join(join)
        if actual is None:
            report.missing.append((join, entity_id, expected))
            continue
        label_ok = _norm(actual) == _norm(expected)
        room_ok = actual_room is None or _norm(actual_room) == _norm(room)
        if not (label_ok and room_ok):
            shown = actual if actual_room is None else f"{actual} in {actual_room}"
            report.mismatched.append((join, entity_id, f"{expected} in {room}", shown))
    return report

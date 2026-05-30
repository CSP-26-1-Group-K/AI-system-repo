from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REQUIRED_REPLAY_FIELDS = {
    "replay_id",
    "task",
    "path",
    "trigger",
}


@dataclass(frozen=True)
class ReplayImportIssue:
    severity: str
    message: str
    replay_id: str | None = None


def load_replay_manifest(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_replay_manifest(path: Path, *, check_paths: bool = True) -> list[ReplayImportIssue]:
    manifest_path = Path(path)
    manifest = load_replay_manifest(manifest_path)
    issues: list[ReplayImportIssue] = []
    if manifest.get("scene_model") != "Merom_0_int":
        issues.append(
            ReplayImportIssue(
                "warning",
                f"scene_model is {manifest.get('scene_model')!r}; current validated demo scene is 'Merom_0_int'",
            )
        )
    if manifest.get("robot_model") not in {"Galaxea_R1", "R1", "R1Pro", "r1", "r1pro"}:
        issues.append(
            ReplayImportIssue(
                "warning",
                f"robot_model is {manifest.get('robot_model')!r}; expected Galaxea_R1 metadata when collaborator replay arrives",
            )
        )

    replays = manifest.get("replays")
    if not isinstance(replays, list) or not replays:
        return [ReplayImportIssue("error", "manifest must contain a non-empty 'replays' list")]

    seen_ids: set[str] = set()
    for index, replay in enumerate(replays):
        replay_id = str(replay.get("replay_id") or f"index_{index}")
        missing = sorted(REQUIRED_REPLAY_FIELDS - set(replay))
        if missing:
            issues.append(ReplayImportIssue("error", f"missing required fields: {missing}", replay_id))
        if replay_id in seen_ids:
            issues.append(ReplayImportIssue("error", "duplicate replay_id", replay_id))
        seen_ids.add(replay_id)

        replay_path = replay.get("path")
        if check_paths and replay_path:
            resolved = Path(replay_path)
            if not resolved.is_absolute():
                resolved = manifest_path.parent / resolved
            if not resolved.exists():
                issues.append(ReplayImportIssue("error", f"replay path does not exist: {resolved}", replay_id))

        trigger = replay.get("trigger")
        if not isinstance(trigger, dict):
            issues.append(ReplayImportIssue("error", "trigger must be an object", replay_id))
        elif not trigger:
            issues.append(ReplayImportIssue("warning", "trigger is empty; replay selection will be ambiguous", replay_id))

    return issues


def issues_to_dicts(issues: list[ReplayImportIssue]) -> list[dict[str, Any]]:
    return [issue.__dict__ for issue in issues]

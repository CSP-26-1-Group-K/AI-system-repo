from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ActivityState:
    enabled: bool = False
    activity_id: str | None = None
    ground_truth_zone: str | None = None
    virtual_sensors: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)


class ActivitySensorSimulator:
    def __init__(self, profiles: dict[str, list[dict[str, Any]]] | None = None, enabled: bool = False):
        self.profiles = profiles or {}
        self.enabled = bool(enabled)

    def start_episode(self, zone: str | None, rng: random.Random) -> ActivityState:
        if not self.enabled or not zone:
            return ActivityState(enabled=self.enabled, ground_truth_zone=zone)
        profile = self._sample_profile(zone, rng)
        if profile is None:
            return ActivityState(enabled=True, ground_truth_zone=zone)
        sensors = copy.deepcopy(profile.get("sensors") or {})
        activity_id = str(profile.get("activity_id") or "unspecified_activity")
        evidence = [f"{key}={value}" for key, value in sensors.items()]
        context = {
            "activity_id": activity_id,
            "ground_truth_zone": zone,
            "zone_estimate": str(profile.get("zone_estimate") or zone),
            "confidence": float(profile.get("confidence", 0.85)),
            "evidence": evidence,
        }
        return ActivityState(
            enabled=True,
            activity_id=activity_id,
            ground_truth_zone=zone,
            virtual_sensors=sensors,
            context=context,
        )

    def estimate_context(
        self,
        activity_state: ActivityState,
        motion_zone: str | None,
        motion_detected: bool,
        last_known_zone: str | None,
    ) -> dict[str, Any]:
        if activity_state.enabled and activity_state.context:
            context = copy.deepcopy(activity_state.context)
            if motion_detected and motion_zone:
                context["zone_estimate"] = motion_zone
                context["confidence"] = max(float(context.get("confidence", 0.0)), 0.95)
                context.setdefault("evidence", []).append(f"motion_detected={motion_zone}")
            elif last_known_zone:
                context.setdefault("evidence", []).append(f"last_known_motion_zone={last_known_zone}")
            return context
        if motion_detected and motion_zone:
            return {
                "activity_id": None,
                "ground_truth_zone": activity_state.ground_truth_zone,
                "zone_estimate": motion_zone,
                "confidence": 0.8,
                "evidence": [f"motion_detected={motion_zone}"],
            }
        return {
            "activity_id": None,
            "ground_truth_zone": activity_state.ground_truth_zone,
            "zone_estimate": last_known_zone or "unknown",
            "confidence": 0.25 if last_known_zone else 0.0,
            "evidence": [f"last_known_motion_zone={last_known_zone}"] if last_known_zone else [],
        }

    def _sample_profile(self, zone: str, rng: random.Random) -> dict[str, Any] | None:
        candidates = self.profiles.get(zone) or []
        if not candidates:
            return None
        total = sum(max(float(profile.get("weight", 1.0)), 0.0) for profile in candidates)
        if total <= 0.0:
            return candidates[0]
        threshold = rng.random() * total
        acc = 0.0
        for profile in candidates:
            acc += max(float(profile.get("weight", 1.0)), 0.0)
            if acc >= threshold:
                return profile
        return candidates[-1]

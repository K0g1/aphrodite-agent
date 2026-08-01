"""Mood system — tracks and updates character emotional state."""

from __future__ import annotations

from ..config import MoodConfig
from ..types import MoodState


class MoodManager:
    """Manages character mood with bounded changes and baseline decay."""

    def __init__(self, config: MoodConfig):
        self.config = config

    def apply_event_impact(
        self, mood: MoodState, valence_delta: float = 0, arousal_delta: float = 0
    ) -> MoodState:
        """Apply bounded event impact to mood."""
        max_delta = self.config.max_delta_per_turn

        new_valence = mood.valence + max(-max_delta, min(max_delta, valence_delta))
        new_arousal = mood.arousal + max(-max_delta, min(max_delta, arousal_delta))

        return MoodState(
            valence=max(-1.0, min(1.0, new_valence)),
            arousal=max(0.0, min(1.0, new_arousal)),
            dominance=mood.dominance,
            affection=mood.affection,
            trust=mood.trust,
            curiosity=mood.curiosity,
        )

    def get_baseline(self) -> MoodState:
        """Get the baseline mood from config."""
        return MoodState(
            valence=self.config.baseline_valence,
            arousal=self.config.baseline_arousal,
            dominance=self.config.baseline_dominance,
            affection=self.config.baseline_affection,
            trust=self.config.baseline_trust,
            curiosity=self.config.baseline_curiosity,
        )

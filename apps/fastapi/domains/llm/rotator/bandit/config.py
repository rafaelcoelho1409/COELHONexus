from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen = True, slots = True)
class FGTSVAConfig:
    """FGTS-VA (NeurIPS 2025, arXiv:2511.02123): per-arm σ̂² replaces fixed scale²; feel-good β adds bonus β·√(ψᵀA^-1ψ)."""
    sigma_init_sq:  float = 0.25     # (0.5)² — matches compose_reward dynamic range
    sigma_min_sq:   float = 0.04     # exploration floor
    var_alpha:      float = 0.1      # EWMA half-life ~7 obs
    feel_good_beta: float = 0.1      # 0.0 → pure variance-aware LinTS


@dataclass(frozen = True, slots = True)
class RewardWeights:
    """Sums to ~1.0 when all signals present."""
    success:      float = 0.30
    schema_valid: float = 0.25
    latency:      float = 0.20
    hash_recall:  float = 0.25


FGTS_VA = FGTSVAConfig()
REWARDS = RewardWeights()

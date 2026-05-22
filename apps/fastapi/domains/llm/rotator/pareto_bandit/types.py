from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np
import time

from .constants import CONTEXT_DIM, FORGETTING_GAMMA, RIDGE_LAMBDA, UCB_ALPHA


@dataclass
class CellState:
    """LinUCB state for one (deployment, dd_process) pair.

    Wire format: JSON-serializable via to_dict()/from_dict(). A_a and b_a
    are stored as nested float lists in Redis (small footprint: 16×16 + 16
    floats ≈ 2KB per cell × 12 deployments × 9 processes = ~216KB total).
    """
    deployment: str
    dd_process: str
    A_a: np.ndarray              # (CONTEXT_DIM, CONTEXT_DIM)
    b_a: np.ndarray              # (CONTEXT_DIM,)
    n_obs: int
    last_updated: float          # unix ts
    benchmark_prior: float       # composite score at warm-start time

    @classmethod
    def fresh(cls, deployment: str, dd_process: str, benchmark_prior: float) -> "CellState":
        """Build a fresh cell, warm-started from the benchmark composite.

        Higher benchmark_prior → tighter prior (smaller covariance, more
        confidence). Below ~0.1 the prior is treated as "unknown" and we
        regularize with RIDGE_LAMBDA only.
        """
        prior = max(0.0, min(1.0, float(benchmark_prior)))
        # Diagonal regularization. Strong prior (high score) ⇒ small A → high confidence.
        # Weak prior ⇒ large A → wide UCB exploration.
        confidence = max(0.1, prior)
        A_a = (RIDGE_LAMBDA / confidence) * np.eye(CONTEXT_DIM, dtype=np.float64)
        # θ̂_a(0) = prior · 1_vec / CONTEXT_DIM → spread the prior across dims
        theta_init = (prior / CONTEXT_DIM) * np.ones(CONTEXT_DIM, dtype=np.float64)
        b_a = A_a @ theta_init
        return cls(
            deployment = deployment,
            dd_process = dd_process,
            A_a = A_a,
            b_a = b_a,
            n_obs = 0,
            last_updated = time.time(),
            benchmark_prior = prior,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "deployment": self.deployment,
            "dd_process": self.dd_process,
            "A_a": self.A_a.tolist(),
            "b_a": self.b_a.tolist(),
            "n_obs": self.n_obs,
            "last_updated": self.last_updated,
            "benchmark_prior": self.benchmark_prior,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CellState":
        return cls(
            deployment = d["deployment"],
            dd_process = d["dd_process"],
            A_a = np.asarray(d["A_a"], dtype = np.float64),
            b_a = np.asarray(d["b_a"], dtype = np.float64),
            n_obs = int(d.get("n_obs", 0)),
            last_updated = float(d.get("last_updated", time.time())),
            benchmark_prior = float(d.get("benchmark_prior", 0.0)),
        )

    def theta_hat(self) -> np.ndarray:
        """θ̂_a = A_a^-1 · b_a (current point estimate of the linear reward params)."""
        return np.linalg.solve(self.A_a, self.b_a)

    def ucb_score(self, context: np.ndarray, alpha: float = UCB_ALPHA) -> tuple[float, float, float]:
        """Compute UCB score = exploit + α · √(explore).

        Returns (total, exploit, explore) so the caller can log all three.
        """
        try:
            theta = self.theta_hat()
        except np.linalg.LinAlgError:
            # Degenerate covariance — fall back to benchmark prior as scalar reward
            return (self.benchmark_prior, self.benchmark_prior, 0.0)
        exploit = float(context @ theta)
        # ψ^T · A^-1 · ψ — explicit solve avoids forming A^-1
        A_inv_psi = np.linalg.solve(self.A_a, context)
        explore = float(context @ A_inv_psi)
        if explore < 0:
            explore = 0.0          # numerical safety
        bonus = alpha * float(np.sqrt(explore))
        return (exploit + bonus, exploit, bonus)

    def apply_update(self, context: np.ndarray, reward: float, *, gamma: float = FORGETTING_GAMMA) -> None:
        """Update A_a and b_a with one observation. Applies geometric forgetting first."""
        keep = 1.0 - gamma
        self.A_a = keep * self.A_a + np.outer(context, context)
        self.b_a = keep * self.b_a + reward * context
        self.n_obs += 1
        self.last_updated = time.time()
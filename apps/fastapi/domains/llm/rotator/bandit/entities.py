from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .config import FGTS_VA
from .params import CONTEXT_DIM, FORGETTING_GAMMA, RIDGE_LAMBDA


logger = logging.getLogger(__name__)


@dataclass
class CellState:
    """Bandit posterior state for one (deployment, dd_process) pair; JSON-serializable."""
    deployment: str
    dd_process: str
    A_a: np.ndarray
    b_a: np.ndarray
    n_obs: int
    last_updated: float
    benchmark_prior: float
    sigma_sq_ewma: float = field(default = FGTS_VA.sigma_init_sq)

    @classmethod
    def fresh(cls, deployment: str, dd_process: str, benchmark_prior: float) -> "CellState":
        prior = max(0.0, min(1.0, float(benchmark_prior)))
        # Higher prior → tighter posterior; below 0.1 → ridge-only "unknown".
        confidence = max(0.1, prior)
        A_a = (RIDGE_LAMBDA / confidence) * np.eye(CONTEXT_DIM, dtype=np.float64)
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
            sigma_sq_ewma = FGTS_VA.sigma_init_sq,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "deployment":      self.deployment,
            "dd_process":      self.dd_process,
            "A_a":             self.A_a.tolist(),
            "b_a":             self.b_a.tolist(),
            "n_obs":           self.n_obs,
            "last_updated":    self.last_updated,
            "benchmark_prior": self.benchmark_prior,
            "sigma_sq_ewma":   self.sigma_sq_ewma,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CellState":
        """Re-inits from benchmark_prior on CONTEXT_DIM drift to prevent matmul failure across deploys."""
        A_a = np.asarray(d["A_a"], dtype=np.float64)
        b_a = np.asarray(d["b_a"], dtype=np.float64)
        expected = (CONTEXT_DIM, CONTEXT_DIM)
        if A_a.shape != expected or b_a.shape != (CONTEXT_DIM,):
            logger.warning(
                f"[pareto] cell dim drift for {d.get('deployment')!r}/"
                f"{d.get('dd_process')!r}: stored A_a {A_a.shape} vs current "
                f"{expected}; re-initializing from benchmark_prior"
            )
            return cls.fresh(d["deployment"], d["dd_process"], float(d.get("benchmark_prior", 0.0)))
        return cls(
            deployment = d["deployment"],
            dd_process = d["dd_process"],
            A_a = A_a,
            b_a = b_a,
            n_obs = int(d.get("n_obs", 0)),
            last_updated = float(d.get("last_updated", time.time())),
            benchmark_prior = float(d.get("benchmark_prior", 0.0)),
            sigma_sq_ewma = float(d.get("sigma_sq_ewma", FGTS_VA.sigma_init_sq)),
        )

    def apply_update(
        self,
        context: np.ndarray,
        reward: float,
        *,
        gamma: float = FORGETTING_GAMMA,
        var_alpha: float = FGTS_VA.var_alpha,
    ) -> None:
        """Order matters: σ² uses PRE-update θ̂ (unbiased residual) before posterior advance."""
        try:
            theta_pre = np.linalg.solve(self.A_a, self.b_a)
            residual = float(reward) - float(context @ theta_pre)
            self.sigma_sq_ewma = (1.0 - var_alpha) * self.sigma_sq_ewma + var_alpha * (residual * residual)
        except np.linalg.LinAlgError:
            pass
        keep = 1.0 - gamma
        self.A_a = keep * self.A_a + np.outer(context, context)
        self.b_a = keep * self.b_a + reward * context
        self.n_obs += 1
        self.last_updated = time.time()

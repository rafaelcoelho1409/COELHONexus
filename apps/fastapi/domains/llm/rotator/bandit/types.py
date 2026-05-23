from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import numpy as np
import time

from .constants import (
    CONTEXT_DIM,
    FGTS_FEEL_GOOD_BETA,
    FGTS_VA_SIGMA_INIT_SQ,
    FGTS_VA_SIGMA_MIN_SQ,
    FGTS_VA_VAR_ALPHA,
    FORGETTING_GAMMA,
    RIDGE_LAMBDA,
    TS_SCALE,
    UCB_ALPHA,
)


@dataclass
class CellState:
    """Bandit posterior state for one (deployment, dd_process) pair.

    Supports three scoring modes that share the same `(A_a, b_a)` sufficient
    statistics; only the score function differs:

      - LinUCB (Li et al. 2010)         — `ucb_score()`
      - LinTS  (Agrawal & Goyal 2013)   — `ts_score()`        (Phase 3a)
      - FGTS-VA (NeurIPS 2025)          — `ts_score_va()`     (Phase 3c)

    FGTS-VA adds one extra serialized field: `sigma_sq_ewma` (per-arm online
    estimate of noise variance, updated via EWMA on squared predictive
    residuals).

    Wire format: JSON-serializable via to_dict()/from_dict(). A_a and b_a
    are stored as nested float lists in Redis (small footprint: 24×24 + 24
    + 1 scalar ≈ 5KB per cell × ~100 cells = ~500KB total).
    """
    deployment: str
    dd_process: str
    A_a: np.ndarray              # (CONTEXT_DIM, CONTEXT_DIM)
    b_a: np.ndarray              # (CONTEXT_DIM,)
    n_obs: int
    last_updated: float          # unix ts
    benchmark_prior: float       # composite score at warm-start time
    # Phase 3c: per-arm noise-variance EWMA used by FGTS-VA scoring.
    # Initialized to FGTS_VA_SIGMA_INIT_SQ; old (LinUCB-era) Redis records
    # without this field default to the same value via from_dict's getter.
    sigma_sq_ewma: float = field(default = FGTS_VA_SIGMA_INIT_SQ)

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
        A_a = (RIDGE_LAMBDA / confidence) * np.eye(CONTEXT_DIM, dtype = np.float64)
        # θ̂_a(0) = prior · 1_vec / CONTEXT_DIM → spread the prior across dims
        theta_init = (prior / CONTEXT_DIM) * np.ones(CONTEXT_DIM, dtype = np.float64)
        b_a = A_a @ theta_init
        return cls(
            deployment = deployment,
            dd_process = dd_process,
            A_a = A_a,
            b_a = b_a,
            n_obs = 0,
            last_updated = time.time(),
            benchmark_prior = prior,
            sigma_sq_ewma = FGTS_VA_SIGMA_INIT_SQ,
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
        return cls(
            deployment      = d["deployment"],
            dd_process      = d["dd_process"],
            A_a             = np.asarray(d["A_a"], dtype = np.float64),
            b_a             = np.asarray(d["b_a"], dtype = np.float64),
            n_obs           = int(d.get("n_obs", 0)),
            last_updated    = float(d.get("last_updated", time.time())),
            benchmark_prior = float(d.get("benchmark_prior", 0.0)),
            # Backward-compatible default for cells written before Phase 3c
            sigma_sq_ewma   = float(d.get("sigma_sq_ewma", FGTS_VA_SIGMA_INIT_SQ)),
        )

    def theta_hat(self) -> np.ndarray:
        """θ̂_a = A_a^-1 · b_a (current point estimate of the linear reward params)."""
        return np.linalg.solve(self.A_a, self.b_a)

    # =========================================================================
    # Scoring — three modes sharing the same (A_a, b_a) state
    # =========================================================================
    def ucb_score(self, context: np.ndarray, alpha: float = UCB_ALPHA) -> tuple[float, float, float]:
        """Mode = LinUCB. Compute UCB score = exploit + α · √(explore).

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

    def ts_score(
        self,
        context: np.ndarray,
        *,
        rng: np.random.Generator,
        scale: float = TS_SCALE,
    ) -> tuple[float, float, float]:
        """Mode = LinTS (Phase 3a). Sample θ̃_a ~ N(A^-1 b, scale²·A^-1) and
        return ψᵀθ̃_a. Return shape mirrors ucb_score's (total, exploit, explore)
        so the predict() dispatch is symmetric — `exploit` here is the sampled
        score itself (no separate exploration bonus in vanilla LinTS), and
        `explore` is the L2 norm of the sampling perturbation so the OTel
        histogram still records exploration magnitude.
        """
        try:
            theta_mean = self.theta_hat()
        except np.linalg.LinAlgError:
            return (self.benchmark_prior, self.benchmark_prior, 0.0)
        try:
            A_inv = np.linalg.inv(self.A_a)
        except np.linalg.LinAlgError:
            return (float(context @ theta_mean), float(context @ theta_mean), 0.0)
        cov = (scale * scale) * A_inv
        # Numerical symmetrization — multivariate_normal is sensitive to drift
        cov = 0.5 * (cov + cov.T)
        try:
            theta_sampled = rng.multivariate_normal(theta_mean, cov, check_valid = "ignore")
        except (np.linalg.LinAlgError, ValueError):
            theta_sampled = theta_mean
        score = float(context @ theta_sampled)
        # Report the perturbation norm as the "explore" channel for OTel parity
        perturbation = float(np.linalg.norm(theta_sampled - theta_mean))
        return (score, float(context @ theta_mean), perturbation)

    def ts_score_va(
        self,
        context: np.ndarray,
        *,
        rng: np.random.Generator,
        sigma_min_sq: float = FGTS_VA_SIGMA_MIN_SQ,
        feel_good_beta: float = FGTS_FEEL_GOOD_BETA,
    ) -> tuple[float, float, float]:
        """Mode = FGTS-VA (Phase 3c). Two augmentations vs ts_score:

          1. Variance-aware: replace the fixed scale² with max(sigma_min_sq,
             self.sigma_sq_ewma) — per-arm online noise estimate, so rate-
             limited providers (high σ̂²) get wider posterior exploration than
             direct-API providers (low σ̂²).
          2. Feel-Good: add `β · √(ψᵀA^-1ψ)` to the sampled score — a small
             optimism bonus that tightens the regret bound vs vanilla LinTS
             (NeurIPS 2025, arXiv:2511.02123).

        Returns (total, exploit, explore) where `exploit` is the variance-
        adapted sampled score and `explore` is the feel-good bonus magnitude.
        """
        try:
            theta_mean = self.theta_hat()
        except np.linalg.LinAlgError:
            return (self.benchmark_prior, self.benchmark_prior, 0.0)
        # Variance-aware scaling: per-arm σ̂² with floor
        sigma_sq = max(float(sigma_min_sq), float(self.sigma_sq_ewma))
        try:
            A_inv = np.linalg.inv(self.A_a)
        except np.linalg.LinAlgError:
            return (float(context @ theta_mean), float(context @ theta_mean), 0.0)
        cov = sigma_sq * A_inv
        cov = 0.5 * (cov + cov.T)
        try:
            theta_sampled = rng.multivariate_normal(theta_mean, cov, check_valid = "ignore")
        except (np.linalg.LinAlgError, ValueError):
            theta_sampled = theta_mean
        exploit = float(context @ theta_sampled)
        # Feel-Good additive optimism bonus
        bonus = 0.0
        if feel_good_beta > 0.0:
            try:
                A_inv_psi = np.linalg.solve(self.A_a, context)
                explore_raw = float(context @ A_inv_psi)
                if explore_raw < 0.0:
                    explore_raw = 0.0
                bonus = float(feel_good_beta) * float(np.sqrt(explore_raw))
            except np.linalg.LinAlgError:
                bonus = 0.0
        return (exploit + bonus, exploit, bonus)

    # =========================================================================
    # Posterior update — invariant across all three modes
    # =========================================================================
    def apply_update(
        self,
        context: np.ndarray,
        reward: float,
        *,
        gamma: float = FORGETTING_GAMMA,
        var_alpha: float = FGTS_VA_VAR_ALPHA,
    ) -> None:
        """Update A_a, b_a, and sigma_sq_ewma with one observation.

        Order matters: variance estimate is updated using the PREDICTIVE
        residual `r - ψᵀθ̂_pre` (i.e. θ̂ computed BEFORE this observation),
        which is the unbiased noise signal for the per-arm σ̂² estimate. Then
        apply geometric forgetting on (A_a, b_a) and add the observation.
        """
        # 1. Update per-arm noise variance estimate (EWMA on squared residual)
        try:
            theta_pre = self.theta_hat()
            predicted = float(context @ theta_pre)
            residual = float(reward) - predicted
            self.sigma_sq_ewma = (1.0 - var_alpha) * self.sigma_sq_ewma + var_alpha * (residual * residual)
        except np.linalg.LinAlgError:
            # Degenerate posterior — keep existing variance estimate unchanged
            pass
        # 2. Posterior update with geometric forgetting (unchanged from LinUCB era)
        keep = 1.0 - gamma
        self.A_a = keep * self.A_a + np.outer(context, context)
        self.b_a = keep * self.b_a + reward * context
        self.n_obs += 1
        self.last_updated = time.time()

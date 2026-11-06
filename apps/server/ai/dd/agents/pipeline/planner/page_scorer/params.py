# The Rotator keeps one bandit cell per (arm, task): scoring calls keep their own, so an arm that answers with an empty vector is
# demoted for scoring only. The name is the one the cell was trained under.
ROTATOR_TASK = "dd.assign"
MODEL_CALLS = 4

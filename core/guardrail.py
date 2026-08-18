# core/guardrail.py
from dataclasses import dataclass
from enum import Enum
from core.actions import ProposedAction
from core.config import MaintainerConfig


class Decision(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"


@dataclass(frozen=True)
class GateResult:
    decision: Decision
    reason: str


def evaluate_action(action: ProposedAction, config: MaintainerConfig) -> GateResult:
    """
    Pure function — zero I/O. Every branch is unit-testable without
    a GitHub mock. Order matters: cheap absolute checks first.
    """
    # 1. Kill switch — absolute override, no other checks matter
    if config.kill_switch_active:
        return GateResult(Decision.DENY, "kill_switch_active")

    # 2. Capability must be explicitly enabled in .ai-maintainer.yaml
    if not config.capability_enabled(action.requires_capability):
        return GateResult(Decision.DENY, f"capability_disabled:{action.requires_capability}")

    # 3. Protected paths — some files/actions are never touched autonomously
    if config.touches_protected_path(action):
        return GateResult(Decision.DENY, "protected_path")

    # 4. Confidence — Tier 2 agents MUST set this.
    #    A missing confidence is a code bug; fail closed, don't fail open.
    if action.confidence is None:
        return GateResult(Decision.DENY, "missing_confidence_fail_closed")

    # 5. Confidence threshold per capability (tunable in config)
    threshold = config.confidence_threshold_for(action.requires_capability)
    if action.confidence < threshold:
        return GateResult(
            Decision.DENY,
            f"confidence_below_threshold:{action.confidence:.2f}<{threshold:.2f}"
        )

    # 6. Rate limit — prevents runaway automation from a bug in a plugin
    if not config.rate_limiter.allow(action):
        return GateResult(Decision.DENY, "rate_limited")

    return GateResult(Decision.ALLOW, "ok")

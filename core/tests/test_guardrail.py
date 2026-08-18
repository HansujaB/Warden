# core/tests/test_guardrail.py
import pytest
from unittest.mock import MagicMock
from core.actions import ProposedAction, ActionType, Target
from core.guardrail import evaluate_action, Decision


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_action(**overrides) -> ProposedAction:
    defaults = dict(
        source_plugin="dep_pr",
        target=Target(repo="kyverno/kyverno", pr_number=1),
        action_type=ActionType.MERGE,
        payload={"merge_method": "squash"},
        rationale="patch bump, CI green",
        requires_capability="dep_pr.auto_merge",
        confidence=1.0,
    )
    defaults.update(overrides)
    return ProposedAction(**defaults)


def make_config(**overrides):
    config = MagicMock()
    config.kill_switch_active = False
    config.capability_enabled.return_value = True
    config.touches_protected_path.return_value = False
    config.confidence_threshold_for.return_value = 0.80
    config.rate_limiter.allow.return_value = True
    for k, v in overrides.items():
        setattr(config, k, v)
    return config


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_allow_when_all_checks_pass():
    result = evaluate_action(make_action(), make_config())
    assert result.decision == Decision.ALLOW
    assert result.reason == "ok"


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------

def test_deny_when_kill_switch_active():
    result = evaluate_action(make_action(), make_config(kill_switch_active=True))
    assert result.decision == Decision.DENY
    assert "kill_switch" in result.reason


def test_kill_switch_short_circuits_before_confidence_check():
    """
    Kill switch must fire before confidence is evaluated.
    A high-confidence action must not slip through a disabled system.
    """
    config = make_config(kill_switch_active=True)
    config.capability_enabled.return_value = False  # also disabled — shouldn't matter
    result = evaluate_action(make_action(confidence=0.99), config)
    assert result.decision == Decision.DENY
    assert "kill_switch" in result.reason


def test_kill_switch_short_circuits_before_capability_check():
    """
    Kill switch is checked before capability — both disabled,
    kill_switch reason should be the one surfaced.
    """
    config = make_config(kill_switch_active=True)
    config.capability_enabled.return_value = False
    result = evaluate_action(make_action(), config)
    assert result.decision == Decision.DENY
    assert "kill_switch" in result.reason
    # capability_enabled should not even be called
    config.capability_enabled.assert_not_called()


# ---------------------------------------------------------------------------
# Capability checks
# ---------------------------------------------------------------------------

def test_deny_when_capability_disabled():
    config = make_config()
    config.capability_enabled.return_value = False
    result = evaluate_action(make_action(), config)
    assert result.decision == Decision.DENY
    assert "capability_disabled" in result.reason


def test_deny_reason_includes_capability_key():
    config = make_config()
    config.capability_enabled.return_value = False
    result = evaluate_action(make_action(requires_capability="dep_pr.auto_merge"), config)
    assert "dep_pr.auto_merge" in result.reason


# ---------------------------------------------------------------------------
# Protected path
# ---------------------------------------------------------------------------

def test_deny_when_protected_path():
    config = make_config()
    config.touches_protected_path.return_value = True
    result = evaluate_action(make_action(), config)
    assert result.decision == Decision.DENY
    assert "protected_path" in result.reason


# ---------------------------------------------------------------------------
# Confidence handling
# ---------------------------------------------------------------------------

def test_deny_when_confidence_missing():
    """Missing confidence from a Tier 2 agent must fail closed, never open."""
    result = evaluate_action(make_action(confidence=None), make_config())
    assert result.decision == Decision.DENY
    assert "missing_confidence" in result.reason


def test_deny_when_confidence_below_threshold():
    config = make_config()
    config.confidence_threshold_for.return_value = 0.90
    result = evaluate_action(make_action(confidence=0.75), config)
    assert result.decision == Decision.DENY
    assert "confidence_below_threshold" in result.reason


def test_deny_when_confidence_just_below_threshold():
    config = make_config()
    config.confidence_threshold_for.return_value = 0.80
    result = evaluate_action(make_action(confidence=0.799), config)
    assert result.decision == Decision.DENY


def test_allow_when_confidence_exactly_at_threshold():
    config = make_config()
    config.confidence_threshold_for.return_value = 0.80
    result = evaluate_action(make_action(confidence=0.80), config)
    assert result.decision == Decision.ALLOW


def test_allow_when_confidence_above_threshold():
    config = make_config()
    config.confidence_threshold_for.return_value = 0.80
    result = evaluate_action(make_action(confidence=0.95), config)
    assert result.decision == Decision.ALLOW


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------

def test_deny_when_rate_limited():
    config = make_config()
    config.rate_limiter.allow.return_value = False
    result = evaluate_action(make_action(), config)
    assert result.decision == Decision.DENY
    assert "rate_limited" in result.reason


# ---------------------------------------------------------------------------
# Check ordering — confidence is checked AFTER protected_path
# ---------------------------------------------------------------------------

def test_protected_path_checked_before_confidence():
    """
    A high-confidence action on a protected path must be denied for
    'protected_path', not evaluated for confidence at all.
    """
    config = make_config()
    config.touches_protected_path.return_value = True
    result = evaluate_action(make_action(confidence=1.0), config)
    assert result.decision == Decision.DENY
    assert "protected_path" in result.reason
    # threshold function should not be called
    config.confidence_threshold_for.assert_not_called()


# ---------------------------------------------------------------------------
# Tier 1 vs Tier 2 confidence conventions
# ---------------------------------------------------------------------------

def test_tier1_confidence_1_always_passes_threshold():
    config = make_config()
    config.confidence_threshold_for.return_value = 1.0
    result = evaluate_action(make_action(confidence=1.0), config)
    assert result.decision == Decision.ALLOW


def test_tier2_zero_confidence_is_denied():
    config = make_config()
    config.confidence_threshold_for.return_value = 0.80
    result = evaluate_action(make_action(confidence=0.0), config)
    assert result.decision == Decision.DENY

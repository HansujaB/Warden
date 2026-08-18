# core/actions.py
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Optional
import hashlib
import json


class ActionType(str, Enum):
    MERGE = "MERGE"
    LABEL = "LABEL"
    COMMENT = "COMMENT"
    REBASE = "REBASE"
    OPEN_PR = "OPEN_PR"
    CI_TRIGGER = "CI_TRIGGER"


@dataclass(frozen=True)
class Target:
    repo: str
    pr_number: Optional[int] = None
    issue_number: Optional[int] = None


@dataclass(frozen=True)
class ProposedAction:
    source_plugin: str
    target: Target
    action_type: ActionType
    payload: dict
    rationale: str
    requires_capability: str
    confidence: Optional[float] = None
    # Tier 1: omit or set 1.0 (deterministic output)
    # Tier 2: REQUIRED — gate treats None as automatic DENY

    def dedup_key(self) -> str:
        """
        Stable hash so the same proposal from a redelivered webhook
        or an overlapping scheduler tick collapses to a single entry.
        Payload is sorted-key serialized so dict ordering doesn't matter.
        """
        basis = json.dumps(
            {
                "source": self.source_plugin,
                "target": self.target.__dict__,
                "type": self.action_type.value,
                "payload": self.payload,
            },
            sort_keys=True,
        )
        return hashlib.sha256(basis.encode()).hexdigest()

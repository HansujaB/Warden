# plugins/dep_pr.py
from typing import Iterable
from core.actions import ProposedAction, ActionType, Target

AUTO_MERGE_TYPES = {"patch", "minor"}
BREAKING_KEYWORDS = ("BREAKING", "removed", "deprecated", "breaking change")


def classify_semver(old: str, new: str) -> str:
    """
    Returns 'major', 'minor', or 'patch'.
    Assumes semver strings of the form MAJOR.MINOR.PATCH.
    Handles 'v' prefix (v1.2.3 → 1.2.3).
    """
    old_parts = [int(x) for x in old.lstrip("v").split(".")]
    new_parts = [int(x) for x in new.lstrip("v").split(".")]

    if new_parts[0] != old_parts[0]:
        return "major"
    if new_parts[1] != old_parts[1]:
        return "minor"
    return "patch"


class DepPrPlugin:
    name = "dep_pr"

    def observe(self, event) -> Iterable[ProposedAction]:
        pr = event.pr
        target = Target(repo=pr.repo, pr_number=pr.number)
        bump = classify_semver(pr.old_version, pr.new_version)

        # Breaking-change scan: cheap keyword match, not semantic analysis.
        # Intentionally conservative: any keyword hit → escalate, don't merge.
        breaking = any(
            kw.lower() in pr.changelog_text.lower()
            for kw in BREAKING_KEYWORDS
        )

        # CI must be green before any merge proposal is emitted.
        if pr.ci_status != "success":
            yield ProposedAction(
                source_plugin=self.name,
                target=target,
                action_type=ActionType.COMMENT,
                payload={"body": f"CI is `{pr.ci_status}` — holding merge review until green."},
                rationale="CI not green; will re-evaluate on status update",
                requires_capability="dep_pr.comment",
                confidence=1.0,
            )
            return

        if bump in AUTO_MERGE_TYPES and not breaking:
            yield ProposedAction(
                source_plugin=self.name,
                target=target,
                action_type=ActionType.MERGE,
                payload={"merge_method": "squash"},
                rationale=(
                    f"{bump} bump, CI green, no breaking-change signals in changelog"
                ),
                requires_capability="dep_pr.auto_merge",
                confidence=1.0,
            )
        else:
            reason = f"{bump} bump"
            if breaking:
                reason += " with breaking-change signal in changelog"
            yield ProposedAction(
                source_plugin=self.name,
                target=target,
                action_type=ActionType.LABEL,
                payload={"label": "needs-human-review"},
                rationale=reason,
                requires_capability="dep_pr.escalate",
                confidence=1.0,
            )

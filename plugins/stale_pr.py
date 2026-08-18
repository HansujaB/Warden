# plugins/stale_pr.py
from datetime import datetime, timezone, timedelta
from typing import Iterable
from core.actions import ProposedAction, ActionType, Target


class StalePrPlugin:
    name = "stale_pr"

    def __init__(self, state_store, nudge_cooldown_hours: int = 48):
        """
        state_store: wraps the generic plugin_state table.
        nudge_cooldown_hours: minimum hours between nudges on the same PR.
        """
        self.state = state_store
        self.cooldown = timedelta(hours=nudge_cooldown_hours)

    def observe(self, event) -> Iterable[ProposedAction]:
        for pr in event.open_prs:
            target = Target(repo=pr.repo, pr_number=pr.number)
            yield from self._evaluate(pr, target)

    def _evaluate(self, pr, target: Target) -> Iterable[ProposedAction]:
        now = datetime.now(tz=timezone.utc)

        # Branch is behind main — try to rebase, or comment if conflicts
        if pr.commits_behind_main > 0:
            if pr.mergeable_cleanly:
                yield ProposedAction(
                    source_plugin=self.name,
                    target=target,
                    action_type=ActionType.REBASE,
                    payload={},
                    rationale=(
                        f"PR is {pr.commits_behind_main} commit(s) behind main "
                        "and merges cleanly — auto-updating branch"
                    ),
                    requires_capability="stale_pr.auto_rebase",
                    confidence=1.0,
                )
            else:
                yield ProposedAction(
                    source_plugin=self.name,
                    target=target,
                    action_type=ActionType.COMMENT,
                    payload={"body": (
                        f"This PR is {pr.commits_behind_main} commit(s) behind `main` "
                        "and has merge conflicts — manual rebase needed."
                    )},
                    rationale="Behind main with conflicts; manual resolution required",
                    requires_capability="stale_pr.comment",
                    confidence=1.0,
                )
            return

        # PR has gone quiet — nudge reviewer/author, but only after cooldown
        idle_hours = (now - pr.last_human_activity_at).total_seconds() / 3600
        cooldown_key = f"last_nudge:{pr.number}"
        last_nudge = self.state.get(self.name, cooldown_key)

        if last_nudge is not None:
            last_nudge_dt = datetime.fromisoformat(last_nudge["timestamp"])
            if now - last_nudge_dt < self.cooldown:
                return  # Still within cooldown window; skip this PR

        if idle_hours > pr.stale_threshold_hours:
            self.state.set(
                self.name, cooldown_key,
                {"timestamp": now.isoformat()}
            )
            yield ProposedAction(
                source_plugin=self.name,
                target=target,
                action_type=ActionType.COMMENT,
                payload={"body": (
                    f"This PR has had no human activity for "
                    f"{idle_hours:.0f} hours — pinging reviewer/author for an update."
                )},
                rationale=f"Idle for {idle_hours:.0f}h, past threshold",
                requires_capability="stale_pr.nudge",
                confidence=1.0,
            )

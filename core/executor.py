# core/executor.py
from core.actions import ProposedAction, ActionType
from core.guardrail import evaluate_action, Decision

ACTION_HANDLERS = {
    ActionType.MERGE:      lambda gh, a: gh.merge(a.target, **a.payload),
    ActionType.LABEL:      lambda gh, a: gh.add_label(a.target, a.payload["label"]),
    ActionType.COMMENT:    lambda gh, a: gh.comment(a.target, a.payload["body"]),
    ActionType.REBASE:     lambda gh, a: gh.update_branch(a.target),
    ActionType.OPEN_PR:    lambda gh, a: gh.open_pr(a.target, **a.payload),
    ActionType.CI_TRIGGER: lambda gh, a: gh.trigger_ci(a.target, a.payload["scope"]),
}


class ActionExecutor:
    def __init__(self, github_client, config, audit_log, dedup_cache):
        self.gh = github_client
        self.config = config
        self.audit = audit_log
        self.dedup = dedup_cache

    def process(self, action: ProposedAction, run_id: str) -> None:
        # 1. Dedup — same action proposal from redelivered webhook or
        #    overlapping scheduler tick → log and skip, don't re-evaluate
        key = action.dedup_key()
        if self.dedup.seen(key):
            self.audit.write(run_id, action, decision="SKIPPED_DUPLICATE", reason=key)
            return

        # 2. Gate — pure function call, no I/O
        result = evaluate_action(action, self.config)

        # 3. Audit — write the decision regardless of allow/deny
        self.audit.write(run_id, action, decision=result.decision.value, reason=result.reason)

        # 4. Act or escalate
        if result.decision is Decision.DENY:
            self._escalate(action, result.reason)
            return

        self.dedup.mark(key)
        ACTION_HANDLERS[action.action_type](self.gh, action)

    def _escalate(self, action: ProposedAction, reason: str) -> None:
        self.gh.add_label(action.target, "needs-human-review")
        self.gh.comment(
            action.target,
            f"Automated action blocked ({reason}): {action.rationale}"
        )

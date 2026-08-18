# core/plugins.py
from typing import Iterable, Protocol
from core.actions import ProposedAction


class TriggerEvent:
    """
    Wrapper around the raw webhook payload or scheduler tick.
    Concrete fields depend on the trigger type; plugins access
    only the fields they need (pr, issue, open_prs, etc.).
    """
    def __init__(self, event_type: str, raw: dict):
        self.event_type = event_type
        self._raw = raw

    def __getattr__(self, name):
        # Convenience: event.pr, event.issue, etc. surface the raw dict values.
        try:
            return self._raw[name]
        except KeyError:
            raise AttributeError(f"TriggerEvent has no attribute '{name}'")


class Tier1Plugin(Protocol):
    """
    Deterministic — no model call, no judgment.
    Pure function of observed context.
    confidence is always 1.0 or omitted.

    Contract:
    - observe() MUST be a pure computation given the event.
    - It MUST NOT call any external API directly.
    - All writes go through ProposedAction → ActionExecutor.
    """

    name: str

    def observe(self, event: TriggerEvent) -> Iterable[ProposedAction]: ...


class Tier2Agent(Protocol):
    """
    Judgment required.
    Fixed system prompt, fixed toolset, one decision per invocation.
    No open-ended looping — hard stop after one structured output.

    Contract:
    - run() MUST set confidence on every ProposedAction it emits.
    - The guardrail gate treats missing confidence as automatic DENY.
    - Write tools (propose_label, post_comment, etc.) MUST route through
      the action queue, never call the GitHub API directly.
    - The agent's GitHub App token MUST be credential-scoped to only the
      action types this agent is allowed to emit.
    """

    name: str

    def run(self, event: TriggerEvent) -> Iterable[ProposedAction]: ...

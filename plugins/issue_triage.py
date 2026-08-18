# plugins/issue_triage.py
import re
from typing import Iterable
from core.actions import ProposedAction, ActionType, Target

# Cheap pre-check patterns — if these match with high confidence,
# skip the LLM call entirely.
# Each tuple: (regex_pattern, label, confidence)
KEYWORD_RULES: list[tuple[str, str, float]] = [
    (r"\bpanic\b.*\bnil pointer\b",                  "kind/bug",           0.95),
    (r"\bfeature request\b|\bproposal\b|\benhancement\b", "kind/feature",  0.90),
    (r"\bdocumentation\b|\bdocs\b|\btypo\b",          "kind/documentation", 0.90),
    (r"\bquestion\b|\bhow (do|to|can)\b",             "kind/question",      0.85),
]

REQUIRED_BUG_TEMPLATE_FIELDS = [
    "Kyverno version",
    "Kubernetes version",
    "Steps to reproduce",
    "Expected behavior",
    "Actual behavior",
]


def keyword_pre_check(issue_body: str) -> tuple[str | None, float]:
    """
    Returns (label, confidence) if a keyword rule matches,
    or (None, 0.0) if no rule is confident enough to decide alone.
    """
    for pattern, label, confidence in KEYWORD_RULES:
        if re.search(pattern, issue_body, re.IGNORECASE):
            return label, confidence
    return None, 0.0


def all_template_fields_present(issue_body: str) -> bool:
    return all(
        field.lower() in issue_body.lower()
        for field in REQUIRED_BUG_TEMPLATE_FIELDS
    )


class IssueTriage:
    name = "issue_triage"
    CONFIDENCE_THRESHOLD = 0.80  # must match .ai-maintainer.yaml

    def __init__(self, llm_client, tool_registry):
        self.llm = llm_client
        self.tools = tool_registry

    def run(self, event) -> Iterable[ProposedAction]:
        issue = event.issue
        target = Target(repo=issue.repo, issue_number=issue.number)

        # --- Pre-check: keyword rules (no model call) ---
        label, confidence = keyword_pre_check(issue.body)

        if label and confidence >= self.CONFIDENCE_THRESHOLD:
            yield ProposedAction(
                source_plugin=self.name,
                target=target,
                action_type=ActionType.LABEL,
                payload={"label": label},
                rationale=(
                    f"Keyword pre-check matched '{label}' "
                    f"at confidence {confidence:.2f}"
                ),
                requires_capability="issue_triage.label",
                confidence=confidence,
            )
            return

        # --- Tier 2 path: LLM judgment ---
        # Issue body is injected with explicit XML-style delimiters, never
        # string-concatenated into the instruction portion of the prompt.
        # This is the prompt-injection defence — the model sees the body
        # as data, not as part of its instructions.
        system_prompt = """You are a Kyverno issue triage assistant.
Your job: classify an issue into exactly one label and identify any required
fields that are missing. You will ONLY emit a structured JSON response.
Do not take any action beyond classification.

Available labels: kind/bug, kind/feature, kind/documentation, kind/question, kind/other
Required bug fields: Kyverno version, Kubernetes version, Steps to reproduce,
                     Expected behavior, Actual behavior
"""

        user_prompt = f"""Classify the following GitHub issue.

<issue_title>
{issue.title}
</issue_title>

<issue_body>
{issue.body}
</issue_body>

Respond with JSON only:
{{
  "label": "<one of the available labels>",
  "confidence": <0.0-1.0>,
  "missing_fields": ["<field>", ...],
  "rationale": "<one sentence>"
}}"""

        result = self.llm.complete(
            system=system_prompt,
            user=user_prompt,
            tools=[
                self.tools.get("search_similar_issues"),  # read-only
                self.tools.get("fetch_repo_docs"),         # read-only
            ],
            response_format="json",
        )

        label = result["label"]
        confidence = float(result["confidence"])
        missing = result.get("missing_fields", [])
        rationale = result.get("rationale", "")

        if confidence < self.CONFIDENCE_THRESHOLD:
            # Not confident enough — escalate, don't guess
            yield ProposedAction(
                source_plugin=self.name,
                target=target,
                action_type=ActionType.LABEL,
                payload={"label": "needs-triage"},
                rationale=f"Low confidence ({confidence:.2f}): {rationale}",
                requires_capability="issue_triage.label",
                confidence=confidence,
            )
            return

        yield ProposedAction(
            source_plugin=self.name,
            target=target,
            action_type=ActionType.LABEL,
            payload={"label": label},
            rationale=rationale,
            requires_capability="issue_triage.label",
            confidence=confidence,
        )

        if missing:
            fields_list = "\n".join(f"- {f}" for f in missing)
            yield ProposedAction(
                source_plugin=self.name,
                target=target,
                action_type=ActionType.COMMENT,
                payload={"body": (
                    "Thanks for the report! To help us investigate, "
                    "could you add the following information?\n\n"
                    f"{fields_list}"
                )},
                rationale=f"Bug report missing required fields: {missing}",
                requires_capability="issue_triage.comment",
                confidence=1.0,
            )

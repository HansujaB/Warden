# plugins/diff_test_map.py
import json
from pathlib import Path
from typing import Iterable
from core.actions import ProposedAction, ActionType, Target

# Files that, if changed, require the full test suite — too much blast radius
# to narrow. Small changes here have project-wide impact.
BROAD_BLAST_RADIUS_PATTERNS = {
    "go.mod",
    "go.sum",
    "Makefile",
    # Generated code and core interfaces often have this property too
    "zz_generated_",
    "pkg/engine/context",
}


class DiffTestMapPlugin:
    name = "diff_test_map"

    def __init__(self, manifest_path: str):
        with open(manifest_path) as f:
            self.manifest: dict[str, list[str]] = json.load(f)
        # Manifest shape:
        # { "pkg/engine/foo.go": ["pkg/engine/foo_test.go",
        #                         "test/conformance/chainsaw/..."] }

    def observe(self, event) -> Iterable[ProposedAction]:
        changed = event.pr.changed_file_paths
        target = Target(repo=event.pr.repo, pr_number=event.pr.number)

        # If any changed file is broad-blast-radius → full suite, no narrowing
        for path in changed:
            if any(pattern in path for pattern in BROAD_BLAST_RADIUS_PATTERNS):
                yield ProposedAction(
                    source_plugin=self.name,
                    target=target,
                    action_type=ActionType.CI_TRIGGER,
                    payload={"scope": "full"},
                    rationale=(
                        f"Changed file '{path}' is broad-blast-radius "
                        "— full suite required"
                    ),
                    requires_capability="diff_test_map.ci_trigger",
                    confidence=1.0,
                )
                return

        # Collect test IDs for all changed files
        test_ids: set[str] = set()
        unmapped: list[str] = []
        for path in changed:
            mapped = self.manifest.get(path)
            if mapped:
                test_ids.update(mapped)
            else:
                unmapped.append(path)

        # Any unmapped path → can't narrow safely, fall back to full suite
        if unmapped:
            yield ProposedAction(
                source_plugin=self.name,
                target=target,
                action_type=ActionType.CI_TRIGGER,
                payload={"scope": "full"},
                rationale=(
                    f"Unmapped path(s) {unmapped} — "
                    "falling back to full suite rather than guessing scope"
                ),
                requires_capability="diff_test_map.ci_trigger",
                confidence=1.0,
            )
            return

        # All paths mapped — run only the identified test subset
        yield ProposedAction(
            source_plugin=self.name,
            target=target,
            action_type=ActionType.CI_TRIGGER,
            payload={"scope": sorted(test_ids)},
            rationale=(
                f"Scoped to {len(test_ids)} test(s) "
                f"covering {len(changed)} changed file(s)"
            ),
            requires_capability="diff_test_map.ci_trigger",
            confidence=1.0,
        )

    @staticmethod
    def build_manifest(repo_root: str, output_path: str) -> None:
        """
        Build-time pass: walk test directories, apply path-convention pairing
        (same-directory source ↔ test), write the manifest JSON.
        Run manually or on repo-structure-changing merges — not on every PR.
        """
        import ast
        import os

        manifest: dict[str, list[str]] = {}
        root = Path(repo_root)

        # Path-convention pass: pair foo.go with foo_test.go in the same dir
        for test_file in root.rglob("*_test.go"):
            source_file = test_file.parent / test_file.name.replace("_test.go", ".go")
            if source_file.exists():
                rel_source = str(source_file.relative_to(root))
                rel_test = str(test_file.relative_to(root))
                manifest.setdefault(rel_source, []).append(rel_test)

        with open(output_path, "w") as f:
            json.dump(manifest, f, indent=2, sort_keys=True)

"""Repository hygiene enforced by tests (supply chain, CI)."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))


def test_actions_pinned_to_commit_sha():
    for workflow in WORKFLOWS:
        for line in workflow.read_text().splitlines():
            match = re.search(r"uses:\s*(\S+)", line)
            if match:
                assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", match.group(1)), (
                    f"{workflow.name}: {match.group(1)} is not pinned to a commit SHA"
                )


def test_workflows_least_privilege_and_no_schedule():
    for workflow in WORKFLOWS:
        data = yaml.safe_load(workflow.read_text())
        triggers = data.get("on", data.get(True))
        assert "schedule" not in triggers, workflow.name
        assert data.get("permissions") == {"contents": "read"}, workflow.name


def test_dependabot_covers_actions():
    config = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text())
    ecosystems = {u["package-ecosystem"] for u in config["updates"]}
    assert "github-actions" in ecosystems

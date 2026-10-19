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


def test_hour_counters_use_app_labels():
    """Attribute names/labels follow the official app (values-it/strings.xml)."""
    import json

    from custom_components.besmart_thermostat.sensor import TECHNICAL_NUMERIC_ATTRIBUTES

    assert TECHNICAL_NUMERIC_ATTRIBUTES["heat_exchanger_hours_amassed"] == "high_condensation_hours"
    assert TECHNICAL_NUMERIC_ATTRIBUTES["worked_hours_in_heating"] == "heating_hours"
    assert TECHNICAL_NUMERIC_ATTRIBUTES["worked_hours_in_dhw"] == "dhw_hours"
    base = ROOT / "custom_components" / "besmart_thermostat"
    it = json.loads((base / "translations" / "it.json").read_text())
    names = {k: v["name"] for k, v in it["entity"]["sensor"]["technical_data"]["state_attributes"].items()}
    # PATCH 0.7.1: every attribute is translated now; the hour counters keep the app labels.
    assert {k: names[k] for k in ("high_condensation_hours", "heating_hours", "dhw_hours")} == {
        "high_condensation_hours": "Ore di funzionamento in regime di elevata condensazione",
        "heating_hours": "Ore di funzionamento in riscaldamento",
        "dhw_hours": "Ore di funzionamento in sanitario",
    }
    from custom_components.besmart_thermostat.sensor import TECHNICAL_ATTRIBUTE_NAMES

    assert set(names) == set(TECHNICAL_ATTRIBUTE_NAMES)
    for f in ("strings.json", "translations/en.json"):
        en = json.loads((base / f).read_text())
        assert set(en["entity"]["sensor"]["technical_data"]["state_attributes"]) == set(names)

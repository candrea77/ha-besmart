"""0.9.0: climatic curve chart drawing (curve_chart.py, no Home Assistant)."""

import logging
import xml.etree.ElementTree as ET

import pytest

from custom_components.besmart_thermostat import curve_chart
from custom_components.besmart_thermostat.api import CLIMATIC_CURVES
from custom_components.besmart_thermostat.curve_chart import ChartValues, needs_redraw, render_svg

NS = {"svg": "http://www.w3.org/2000/svg"}
PIANO_1 = ChartValues(
    curve=0.6, max_flow=35, min_flow=24, influence=10, outdoor=15.3,
    requested=26, requested_source="thermostat", heat_request=True,
)


def parse(values, warn_key=""):
    """Render and parse: the SVG must be valid XML."""
    return ET.fromstring(render_svg(values, warn_key))


def of_class(root, tag, css):
    return [e for e in root.iter(f"{{{NS['svg']}}}{tag}") if e.get("class") == css]


def texts(root):
    return [e.text for e in root.iter(f"{{{NS['svg']}}}text")]


def selected_flows(root):
    """Flow temperatures (°C) of the selected curve limited between min and max."""
    (line,) = of_class(root, "polyline", "sel")
    span = curve_chart.FLOW_RANGE[1] - curve_chart.FLOW_RANGE[0]
    height = curve_chart.PLOT_BOTTOM - curve_chart.PLOT_TOP
    return [
        curve_chart.FLOW_RANGE[0] + (curve_chart.PLOT_BOTTOM - float(p.split(",")[1])) * span / height
        for p in line.get("points").split()
    ]


def test_the_table_has_the_ten_curves_of_the_app():
    assert curve_chart.CURVES == CLIMATIC_CURVES
    for curve, points in curve_chart.CURVE_TABLE.items():
        assert len(points) == len(curve_chart.OUTDOOR_POINTS)
        assert list(points) == sorted(points), curve  # colder outside -> hotter flow
        assert points[0] == (20.0 if curve <= 0.8 else 27.0), curve


@pytest.mark.parametrize("curve", CLIMATIC_CURVES)
@pytest.mark.parametrize("language", ["en", "it"])
def test_valid_svg_for_every_curve(curve, language):
    root = parse(ChartValues(curve=curve, max_flow=80, min_flow=10, influence=5, outdoor=0, language=language))
    assert root.get("viewBox") == "0 0 480 316"
    assert len(of_class(root, "polyline", "curve")) == 10
    assert len(of_class(root, "polyline", "sel")) == 1
    assert len(of_class(root, "circle", "work")) == 1
    label = "Curve" if language == "en" else "Curva"
    assert f"{label} {curve:g}" in texts(root)


def test_piano_1_values():
    """Curve 0.6 at 15.3 °C: about 23.3 °C, limited to the 24 °C minimum."""
    assert curve_chart.curve_flow(0.6, 15.3) == pytest.approx(23.34, abs=0.01)
    assert curve_chart.limit_flow(23.34, 24, 35) == 24
    root = parse(PIANO_1)
    assert "Outdoor 15.3 °C → curve 24 °C" in texts(root)
    assert "Requested flow 26 °C (thermostat)" in texts(root)
    assert len(of_class(root, "circle", "req")) == 1
    assert len(of_class(root, "line", "reqline")) == 1


def test_max_below_the_curve_limits_it():
    root = parse(ChartValues(curve=1.5, max_flow=35, min_flow=20, outdoor=-10))
    flows = selected_flows(root)
    assert max(flows) == pytest.approx(35, abs=0.05)
    assert curve_chart.limit_flow(curve_chart.curve_flow(1.5, -10), 20, 35) == 35
    # The free curve is still drawn (dashed) above the limit.
    assert len(of_class(root, "polyline", "selfree")) == 1
    (max_line,) = of_class(root, "line", "max")
    assert max_line.get("y1") == max_line.get("y2")


def test_min_above_the_curve_limits_it():
    root = parse(ChartValues(curve=0.2, max_flow=60, min_flow=30, outdoor=10))
    assert min(selected_flows(root)) == pytest.approx(30, abs=0.05)
    assert max(selected_flows(root)) == pytest.approx(30, abs=0.05)  # 0.2 never reaches 30
    assert curve_chart.limit_flow(curve_chart.curve_flow(0.2, 10), 30, 60) == 30


def test_missing_values_are_not_drawn_never_a_zero_line():
    root = parse(ChartValues(curve=None, max_flow=None, min_flow=None, influence=None, outdoor=None))
    assert not of_class(root, "line", "max") and not of_class(root, "line", "min")
    assert not of_class(root, "polyline", "sel")
    assert not root.findall(".//svg:circle[@class='work']", NS)
    for label in ("Curve", "Max flow", "Min flow", "Room sensor influence", "Outdoor"):
        assert f"{label}: not available" in texts(root)
    # 10 grey curves of the manual stay.
    assert len(of_class(root, "polyline", "curve")) == 10


def test_without_outdoor_temperature_no_points():
    root = parse(ChartValues(curve=0.6, max_flow=35, min_flow=24, influence=10, requested=26,
                             requested_source="thermostat", heat_request=True))
    assert not of_class(root, "circle", "work") and not of_class(root, "circle", "req")
    assert "Outdoor: not available" in texts(root)


def test_no_heat_request():
    root = parse(ChartValues(curve=0.6, max_flow=35, min_flow=24, outdoor=15.3))
    assert "Requested flow: no heat request" in texts(root)
    assert of_class(root, "circle", "work") and not of_class(root, "circle", "req")


def test_boiler_offline_keeps_curve_max_min_without_points():
    root = parse(ChartValues(curve=0.6, max_flow=35, min_flow=24, influence=10,
                             status=curve_chart.STATUS_BOILER_OFFLINE, language="it"))
    assert of_class(root, "polyline", "sel") and of_class(root, "line", "max") and of_class(root, "line", "min")
    assert not of_class(root, "circle", "work") and not of_class(root, "circle", "req")
    assert "T esterna: caldaia non raggiungibile" in texts(root)


def test_unsupported_unit_draws_no_curves_or_points():
    root = parse(ChartValues(curve=0.6, max_flow=35, min_flow=24, outdoor=15.3, requested=26,
                             status=curve_chart.STATUS_UNSUPPORTED_UNIT, language="it"))
    assert not root.findall(".//svg:polyline", NS) and not root.findall(".//svg:circle", NS)
    assert not of_class(root, "line", "max") and not of_class(root, "line", "min")
    assert "unità non supportata" in texts(root)


def test_curve_not_offered_by_the_app_is_interpolated_with_one_warning(caplog):
    caplog.set_level(logging.WARNING)
    flow = curve_chart.curve_flow(0.7, 0, "***t0.7")
    assert flow == pytest.approx((32.3 + 36.1) / 2)
    for _ in range(3):
        parse(ChartValues(curve=0.7, outdoor=0), "***t0.7")
    warnings = [r for r in caplog.records if "not one of the values of the app" in r.getMessage()]
    assert len(warnings) == 1
    assert curve_chart.curve_points(0.1) is None and curve_chart.curve_points(3.5) is None


# ------------------------------------------------------------------ redraw


def test_threshold_not_rounding():
    drawn = PIANO_1
    # 15.2 <-> 15.3 forever: never redrawn.
    for outdoor in (15.2, 15.3) * 10:
        assert not needs_redraw(drawn, ChartValues(**{**PIANO_1.__dict__, "outdoor": outdoor}))
    # Slow drift from 15.2: redrawn only 0.5 °C away from the drawn value.
    drawn = ChartValues(**{**PIANO_1.__dict__, "outdoor": 15.2})
    for outdoor, redraw in ((15.4, False), (15.6, False), (15.7, True), (15.8, True)):
        new = ChartValues(**{**PIANO_1.__dict__, "outdoor": outdoor})
        assert needs_redraw(drawn, new) is redraw, outdoor
    # Same rule for the requested flow.
    for requested, redraw in ((26.4, False), (25.6, False), (26.5, True), (25.5, True)):
        assert needs_redraw(PIANO_1, ChartValues(**{**PIANO_1.__dict__, "requested": requested})) is redraw


@pytest.mark.parametrize(
    "change",
    [
        {"curve": 0.8}, {"max_flow": 36}, {"min_flow": 23}, {"influence": 11},
        {"outdoor": None}, {"requested": None}, {"requested_source": "boiler"},
        {"heat_request": False}, {"status": curve_chart.STATUS_BOILER_OFFLINE},
        {"language": "it"},
    ],
)
def test_any_other_change_redraws_at_once(change):
    assert needs_redraw(PIANO_1, ChartValues(**{**PIANO_1.__dict__, **change}))
    assert needs_redraw(None, PIANO_1)
    assert not needs_redraw(PIANO_1, ChartValues(**PIANO_1.__dict__))


# --------------------------------------------------------------- languages


def test_language_tables_are_aligned():
    assert set(curve_chart.TEXTS) == {"en", "it"}
    assert set(curve_chart.TEXTS["en"]) == set(curve_chart.TEXTS["it"])
    for language, table in curve_chart.TEXTS.items():
        for key, text in table.items():
            assert text.strip(), (language, key)


@pytest.mark.parametrize(
    ("language", "expected"),
    [("it", "it"), ("it-IT", "it"), ("en", "en"), ("en-GB", "en"), ("de", "en"), (None, "en")],
)
def test_chart_language(language, expected):
    assert curve_chart.chart_language(language) == expected


def test_svg_texts_follow_the_language():
    en = texts(parse(PIANO_1))
    it = texts(parse(ChartValues(**{**PIANO_1.__dict__, "language": "it"})))
    assert "Requested flow 26 °C (thermostat)" in en and "Room sensor influence 10" in en
    assert "Mandata richiesta 26 °C (termostato)" in it and "Influenza sensore 10" in it
    assert any(t.startswith("Indicativo, dal manuale Riello") for t in it)
    # Unknown language: English.
    assert texts(parse(ChartValues(**{**PIANO_1.__dict__, "language": "de"}))) == en


def test_every_text_is_xml_escaped(monkeypatch):
    nasty = 'a < b & "c" \'d\' </text><script>x</script>'
    monkeypatch.setitem(curve_chart.TEXTS["en"], "note", nasty)
    monkeypatch.setitem(curve_chart.TEXTS["en"], "curve", nasty)
    svg = render_svg(PIANO_1)
    root = ET.fromstring(svg)
    assert nasty in texts(root)
    assert "<script>" not in svg

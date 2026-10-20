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


def flow_of_y(y, axis):
    """Flow temperature (°C) of a y coordinate on the given axis."""
    span = axis[1] - axis[0]
    height = curve_chart.PLOT_BOTTOM - curve_chart.PLOT_TOP
    return axis[0] + (curve_chart.PLOT_BOTTOM - float(y)) * span / height


def selected_flows(root, values):
    """Flow temperatures (°C) of the selected curve limited between min and max."""
    (line,) = of_class(root, "polyline", "sel")
    axis = curve_chart.flow_axis(values)
    return [flow_of_y(p.split(",")[1], axis) for p in line.get("points").split()]


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
    assert "Outdoor 15.3 °C → curve 23.3 °C, limited to 24 °C" in texts(root)
    assert "Requested flow 26 °C (thermostat)" in texts(root)
    assert len(of_class(root, "circle", "req")) == 1
    assert len(of_class(root, "line", "reqline")) == 1


def test_max_below_the_curve_limits_it():
    values = ChartValues(curve=1.5, max_flow=35, min_flow=20, outdoor=-10)
    root = parse(values)
    flows = selected_flows(root, values)
    assert max(flows) == pytest.approx(35, abs=0.05)
    assert curve_chart.limit_flow(curve_chart.curve_flow(1.5, -10), 20, 35) == 35
    # The free curve is still drawn (dashed) above the limit.
    assert len(of_class(root, "polyline", "selfree")) == 1
    (max_line,) = of_class(root, "line", "max")
    assert max_line.get("y1") == max_line.get("y2")


def test_min_above_the_curve_limits_it():
    values = ChartValues(curve=0.2, max_flow=60, min_flow=30, outdoor=10)
    root = parse(values)
    assert min(selected_flows(root, values)) == pytest.approx(30, abs=0.05)
    assert max(selected_flows(root, values)) == pytest.approx(30, abs=0.05)  # 0.2 never reaches 30
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


# ----------------------------------------------- 0.9.0b2: axis, off scale

SVG = "{http://www.w3.org/2000/svg}"


def coordinates(root):
    """Every (x, y) drawn: lines, polylines, polygons, circles (with radius), texts."""
    result = []
    for e in root.iter():
        tag = e.tag.removeprefix(SVG)
        if tag == "line":
            result += [(float(e.get("x1")), float(e.get("y1"))), (float(e.get("x2")), float(e.get("y2")))]
        elif tag in ("polyline", "polygon"):
            result += [tuple(map(float, p.split(","))) for p in e.get("points").split()]
        elif tag == "circle":
            cx, cy, r = float(e.get("cx")), float(e.get("cy")), float(e.get("r"))
            result += [(cx - r, cy - r), (cx + r, cy + r)]
        elif tag == "text":
            result.append((float(e.get("x")), float(e.get("y"))))
    return result


def assert_inside(root):
    for x, y in coordinates(root):
        assert 0 <= x <= curve_chart.WIDTH and 0 <= y <= curve_chart.HEIGHT, (x, y)
    # Lines and curves of the chart stay in the plot area (they are clipped too).
    for e in root.iter():
        if e.tag.removeprefix(SVG) == "polyline" or e.get("class") in ("max", "min"):
            assert e.get("clip-path") == "url(#plot)"
            points = (
                [tuple(map(float, p.split(","))) for p in e.get("points").split()]
                if e.get("points") else
                [(float(e.get("x1")), float(e.get("y1"))), (float(e.get("x2")), float(e.get("y2")))]
            )
            for x, y in points:
                assert curve_chart.PLOT_LEFT - 0.05 <= x <= curve_chart.PLOT_RIGHT + 0.05, (x, e.get("class"))
                assert curve_chart.PLOT_TOP - 0.05 <= y <= curve_chart.PLOT_BOTTOM + 0.05, (y, e.get("class"))


def values(**kwargs):
    return ChartValues(**{**PIANO_1.__dict__, **kwargs})


@pytest.mark.parametrize(
    ("max_flow", "min_flow", "axis"),
    [
        (15, 10, (10, 50)),
        (35, 24, (20, 50)),
        (40, 24, (20, 60)),
        (45, 30, (20, 60)),
        (80, 30, (20, 100)),
        (35, 15, (10, 50)),
        (None, 24, (20, 100)),
        (None, 12, (10, 100)),
        (None, None, (20, 100)),
    ],
)
def test_flow_axis(max_flow, min_flow, axis):
    assert curve_chart.flow_axis(values(max_flow=max_flow, min_flow=min_flow)) == axis


def test_flow_axis_unsupported_unit():
    assert curve_chart.flow_axis(values(max_flow=35, status=curve_chart.STATUS_UNSUPPORTED_UNIT)) == (20, 100)


@pytest.mark.parametrize(
    ("max_flow", "min_flow", "step"),
    [(35, 24, 5), (15, 10, 5), (40, 24, 5), (80, 30, 10), (None, None, 10)],
)
def test_grid_step(max_flow, min_flow, step):
    root = parse(values(max_flow=max_flow, min_flow=min_flow))
    bottom, top = curve_chart.flow_axis(values(max_flow=max_flow, min_flow=min_flow))
    labels = [int(t.text) for t in root.iter(f"{SVG}text") if t.get("class") == "axis" and float(t.get("x")) == curve_chart.PLOT_LEFT - 4]
    assert labels == list(range(int(bottom), int(top) + 1, step))


@pytest.mark.parametrize("curve", CLIMATIC_CURVES)
@pytest.mark.parametrize(("max_flow", "min_flow"), [(35, 24), (15, 10), (80, 30), (35, 12)])
def test_curves_on_every_axis(curve, max_flow, min_flow):
    vals = values(curve=curve, max_flow=max_flow, min_flow=min_flow)
    root = parse(vals)
    axis = curve_chart.flow_axis(vals)
    assert_inside(root)
    for line, (table_curve, points) in zip(of_class(root, "polyline", "curve"), curve_chart.CURVE_TABLE.items()):
        first = line.get("points").split()[0]
        assert flow_of_y(first.split(",")[1], axis) == pytest.approx(points[0], abs=0.05), table_curve
        assert min(flow_of_y(p.split(",")[1], axis) for p in line.get("points").split()) >= 20 - 0.05
    flows = selected_flows(root, vals)
    assert max(flows) <= max_flow + 0.05 and min(flows) >= min_flow - 0.05
    if max_flow == 15:
        assert flows == pytest.approx([15] * len(flows), abs=0.05)


@pytest.mark.parametrize(("max_flow", "min_flow"), [(35, 24), (40, 24), (80, 30), (None, None)])
def test_every_curve_has_a_label_without_overlaps(max_flow, min_flow):
    root = parse(values(max_flow=max_flow, min_flow=min_flow))
    labels = [t for t in root.iter(f"{SVG}text") if t.get("class") == "clabel"]
    assert sorted(t.text for t in labels) == sorted(f"{c:g}" for c in CLIMATIC_CURVES)
    boxes = []
    for t in labels:
        x, y, width = float(t.get("x")), float(t.get("y")), 5.5 * len(t.text)
        if t.get("text-anchor") == "middle":
            x -= width / 2
        boxes.append((x, y - 8, x + width, y))
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1], (a, b)


def test_piano_1_points_are_apart():
    root = parse(PIANO_1)
    (work,), (req,) = of_class(root, "circle", "work"), of_class(root, "circle", "req")
    assert abs(float(work.get("cy")) - float(req.get("cy"))) >= 10
    assert_inside(root)


@pytest.mark.parametrize(
    ("language", "above", "below"),
    [
        ("en", "Outdoor 23.1 °C (off scale, value at 20 °C) → curve 20 °C, limited to 24 °C",
         "Outdoor -18 °C (off scale, value at -16 °C) → curve 38.5 °C, limited to 35 °C"),
        ("it", "T esterna 23.1 °C (fuori scala, valore a 20 °C) → curva 20 °C, limitata a 24 °C",
         "T esterna -18 °C (fuori scala, valore a -16 °C) → curva 38.5 °C, limitata a 35 °C"),
    ],
)
def test_outdoor_off_scale(language, above, below):
    for outdoor, text, edge_x, direction in (
        (23.1, above, curve_chart.PLOT_LEFT, -1), (-18, below, curve_chart.PLOT_RIGHT, 1),
    ):
        vals = values(outdoor=outdoor, language=language)
        root = parse(vals)
        assert_inside(root)
        assert curve_chart.outdoor_off_scale(outdoor)
        assert not of_class(root, "circle", "work")
        (point,) = of_class(root, "circle", "workoff")
        assert float(point.get("cx")) == pytest.approx(edge_x)
        triangles = of_class(root, "polygon", "offscale")
        assert len(triangles) == 1
        tip, base = (float(p.split(",")[0]) for p in triangles[0].get("points").split()[:2])
        # The tip points outwards (left above 20 °C, right below -16 °C).
        assert (tip - base) * direction > 0
        assert text in texts(root)
    # The curve value at the edge comes from the table, never extrapolated.
    assert curve_chart.curve_flow(0.6, 23.1) == 20.0
    assert curve_chart.curve_flow(0.6, -18) == 38.5
    assert curve_chart.curve_flow(3.0, -30) == 121.0


def test_outdoor_on_the_edges_is_not_off_scale():
    for outdoor in (20, -16):
        root = parse(values(outdoor=outdoor))
        assert not curve_chart.outdoor_off_scale(outdoor)
        assert not of_class(root, "polygon", "offscale") and of_class(root, "circle", "work")
        assert not any("off scale" in (t or "") for t in texts(root))


@pytest.mark.parametrize(
    ("language", "requested", "max_flow", "min_flow", "direction", "text"),
    [
        ("en", 60, 35, 24, "up", "Requested flow 60 °C, off scale (thermostat)"),
        ("it", 60, 35, 24, "up", "Mandata richiesta 60 °C, fuori scala (termostato)"),
        ("en", 15, 35, 24, "down", "Requested flow 15 °C, off scale (thermostat)"),
        ("it", 15, 35, 24, "down", "Mandata richiesta 15 °C, fuori scala (termostato)"),
        ("en", 15, 35, 12, None, "Requested flow 15 °C (thermostat)"),
        ("it", 15, 35, 12, None, "Mandata richiesta 15 °C (termostato)"),
    ],
)
def test_requested_off_scale(language, requested, max_flow, min_flow, direction, text):
    vals = values(requested=requested, max_flow=max_flow, min_flow=min_flow, language=language)
    root = parse(vals)
    assert_inside(root)
    assert text in texts(root)
    assert curve_chart.requested_off_scale(vals) is (direction is not None)
    (req,) = of_class(root, "circle", "req")
    triangles = of_class(root, "polygon", "offscale")
    if direction is None:
        assert not triangles
        return
    (triangle,) = triangles
    edge = curve_chart.PLOT_TOP if direction == "up" else curve_chart.PLOT_BOTTOM
    assert float(req.get("cy")) == pytest.approx(edge)
    tip_y = float(triangle.get("points").split()[0].split(",")[1])
    base_y = float(triangle.get("points").split()[1].split(",")[1])
    assert (tip_y < base_y) is (direction == "up")


def test_both_off_scale():
    root = parse(values(outdoor=25, requested=70, language="it"))
    assert_inside(root)
    assert len(of_class(root, "polygon", "offscale")) == 2
    assert any("T esterna 25 °C (fuori scala" in t for t in texts(root))
    assert any("Mandata richiesta 70 °C, fuori scala" in t for t in texts(root))


@pytest.mark.parametrize(
    ("language", "kwargs", "text"),
    [
        ("en", {}, "Outdoor 15.3 °C → curve 23.3 °C, limited to 24 °C"),
        ("it", {}, "T esterna 15.3 °C → curva 23.3 °C, limitata a 24 °C"),
        ("en", {"outdoor": -10, "curve": 1.5}, "Outdoor -10 °C → curve 67.8 °C, limited to 35 °C"),
        ("it", {"outdoor": -10, "curve": 1.5}, "T esterna -10 °C → curva 67.8 °C, limitata a 35 °C"),
        ("en", {"outdoor": 4, "max_flow": 60, "min_flow": 20}, "Outdoor 4 °C → curve 30.2 °C"),
        ("it", {"outdoor": 4, "max_flow": 60, "min_flow": 20}, "T esterna 4 °C → curva 30.2 °C"),
        # Same value once rounded: never "limited to" the shown value.
        ("en", {"outdoor": 4, "max_flow": 60, "min_flow": 30.2}, "Outdoor 4 °C → curve 30.2 °C"),
        ("it", {"outdoor": 4, "max_flow": 60, "min_flow": 30.2}, "T esterna 4 °C → curva 30.2 °C"),
    ],
)
def test_working_point_legend(language, kwargs, text):
    root = parse(values(language=language, **kwargs))
    assert text in texts(root)


@pytest.mark.parametrize("language", ["en", "it"])
@pytest.mark.parametrize(
    "kwargs",
    [
        {}, {"outdoor": 23.1}, {"outdoor": -18}, {"requested": 60}, {"requested": 15},
        {"outdoor": 25, "requested": 70}, {"max_flow": 15, "min_flow": 10, "requested": 12},
        {"max_flow": None, "min_flow": None}, {"status": curve_chart.STATUS_BOILER_OFFLINE},
        {"status": curve_chart.STATUS_UNSUPPORTED_UNIT}, {"outdoor": None}, {"curve": None},
    ],
)
def test_nothing_outside_the_image(language, kwargs):
    assert_inside(parse(values(language=language, **kwargs)))

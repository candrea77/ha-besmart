"""PATCH 0.9: climatic curve chart (SVG), pure Python.

Curves of the Riello manual (heating flow temperature as a function of the
outdoor temperature), traced from the manual's chart: one point every 4 °C
from 20 to -16 °C, linear interpolation in between. The chart is indicative,
not the boiler's own calculation. No Home Assistant import here: the image
entity (image.py) collects the values, this module only computes and draws.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from xml.sax.saxutils import escape

_LOGGER = logging.getLogger(__name__)

# Outdoor temperatures of the table (x axis, left to right).
OUTDOOR_POINTS = (20.0, 16.0, 12.0, 8.0, 4.0, 0.0, -4.0, -8.0, -12.0, -16.0)
# Flow temperature (°C) of each curve at OUTDOOR_POINTS. 2.5 and 3.0 leave
# the manual's chart (100 °C) at about -13 and -5.5 °C: the last values are
# extrapolated and never drawn above 100 °C.
CURVE_TABLE: dict[float, tuple[float, ...]] = {
    0.2: (20.0, 20.8, 22.1, 23.2, 23.9, 24.7, 25.3, 25.7, 26.0, 26.2),
    0.4: (20.0, 21.5, 23.5, 25.3, 26.8, 28.3, 29.6, 30.8, 31.9, 32.5),
    0.6: (20.0, 22.9, 25.4, 28.0, 30.2, 32.3, 34.1, 35.8, 37.1, 38.5),
    0.8: (20.0, 24.1, 27.5, 30.7, 33.5, 36.1, 38.5, 40.8, 42.7, 44.2),
    1.0: (27.0, 32.5, 36.7, 40.6, 43.9, 47.2, 50.1, 52.9, 55.5, 57.4),
    1.2: (27.0, 33.4, 38.6, 43.5, 48.1, 52.3, 56.2, 59.5, 62.7, 65.3),
    1.5: (27.0, 34.9, 41.2, 47.1, 52.3, 57.3, 61.8, 66.0, 69.6, 72.8),
    2.0: (27.0, 37.3, 45.7, 53.7, 60.8, 67.4, 73.4, 79.1, 83.9, 88.1),
    2.5: (27.0, 40.2, 51.0, 60.9, 69.1, 77.3, 84.5, 91.6, 97.9, 104.0),
    3.0: (27.0, 42.3, 55.2, 67.2, 78.0, 88.0, 96.7, 105.0, 113.0, 121.0),
}
CURVES = tuple(CURVE_TABLE)
CURVE_RANGE = (CURVES[0], CURVES[-1])

# Chart axes. PATCH 0.9 (b2): the flow axis follows the max/min flow
# temperature (see flow_axis()); FLOW_RANGE is the axis without them.
OUTDOOR_RANGE = (20.0, -16.0)
FLOW_RANGE = (20.0, 100.0)
FLOW_AXIS_LOW = 10.0  # bottom of the axis when min or max is below 20 °C
FLOW_AXIS_MARGIN = 15.0  # top of the axis: max + 15 ...
FLOW_AXIS_MIN_TOP = 50.0  # ... at least 50, rounded up to a multiple of 10
# A requested flow temperature outside these limits is not plausible (e.g. the
# 5 °C the thermostat reports when it is not calling for heat).
PLAUSIBLE_FLOW = (10.0, 100.0)
# Outdoor temperature / requested flow: redraw only from this difference on.
REDRAW_THRESHOLD = 0.5

STATUS_OK = "ok"
STATUS_UNSUPPORTED_UNIT = "unsupported_unit"
STATUS_BOILER_OFFLINE = "boiler_offline"

SOURCE_THERMOSTAT = "thermostat"
SOURCE_BOILER = "boiler"

# Texts drawn in the SVG, per language (fallback "en"). Same keys in every
# language (checked by the tests); every text is XML-escaped when drawn.
TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "flow_axis": "Flow °C",
        "outdoor_axis": "Outdoor °C",
        "curve": "Curve",
        "max": "Max flow",
        "min": "Min flow",
        "influence": "Room sensor influence",
        "outdoor": "Outdoor",
        "curve_flow": "curve",
        "requested": "Requested flow",
        "source_thermostat": "thermostat",
        "source_boiler": "boiler",
        "not_available": "not available",
        "no_heat_request": "no heat request",
        "boiler_offline": "boiler unreachable",
        "unsupported_unit": "unsupported unit",
        "limited_to": "limited to",
        "off_scale": "off scale",
        "value_at": "value at",
        "note": "Indicative, from the Riello manual: T flow = T curve + (CLI × InFL × ΔT room)",
    },
    "it": {
        "flow_axis": "Mandata °C",
        "outdoor_axis": "T esterna °C",
        "curve": "Curva",
        "max": "Mandata max",
        "min": "Mandata min",
        "influence": "Influenza sensore",
        "outdoor": "T esterna",
        "curve_flow": "curva",
        "requested": "Mandata richiesta",
        "source_thermostat": "termostato",
        "source_boiler": "caldaia",
        "not_available": "non disponibile",
        "no_heat_request": "nessuna richiesta di calore",
        "boiler_offline": "caldaia non raggiungibile",
        "unsupported_unit": "unità non supportata",
        "limited_to": "limitata a",
        "off_scale": "fuori scala",
        "value_at": "valore a",
        "note": "Indicativo, dal manuale Riello: T mand risc = T mand curva + (CLI × InFL × ΔT amb)",
    },
}


def chart_language(language: str | None) -> str:
    """Home Assistant language -> chart language ("it..." -> it, else en)."""
    return "it" if str(language or "").lower().startswith("it") else "en"


@dataclass(frozen=True)
class ChartValues:
    """Everything the chart shows; equal values give the same SVG."""

    curve: float | None = None
    max_flow: float | None = None
    min_flow: float | None = None
    influence: float | None = None
    outdoor: float | None = None
    requested: float | None = None
    requested_source: str | None = None
    # True: the thermostat is calling for heat (requested flow expected).
    heat_request: bool = False
    status: str = STATUS_OK
    language: str = "en"


def _differs(old: float | None, new: float | None) -> bool:
    """Appeared/disappeared, or moved by at least REDRAW_THRESHOLD."""
    if old is None or new is None:
        return (old is None) != (new is None)
    return abs(new - old) >= REDRAW_THRESHOLD - 1e-9


def needs_redraw(drawn: ChartValues | None, new: ChartValues) -> bool:
    """Compare the new values with those of the image last drawn.

    Curve, max, min, influence, status, language or the source of the
    requested flow: any change. Outdoor temperature and requested flow: a
    value that appears or disappears, or a difference of at least 0.5 °C from
    the drawn value (a threshold, not rounding: no flapping at a boundary).
    """
    if drawn is None:
        return True
    if (
        drawn.curve, drawn.max_flow, drawn.min_flow, drawn.influence,
        drawn.status, drawn.language, drawn.requested_source, drawn.heat_request,
    ) != (
        new.curve, new.max_flow, new.min_flow, new.influence,
        new.status, new.language, new.requested_source, new.heat_request,
    ):
        return True
    return _differs(drawn.outdoor, new.outdoor) or _differs(drawn.requested, new.requested)


def _interp(x: float, xs: tuple[float, ...], ys: tuple[float, ...]) -> float:
    """Linear interpolation on a decreasing x table, clamped at its ends."""
    if x >= xs[0]:
        return ys[0]
    if x <= xs[-1]:
        return ys[-1]
    for i in range(len(xs) - 1):
        x0, x1 = xs[i], xs[i + 1]
        if x1 <= x <= x0:
            return ys[i] + (ys[i + 1] - ys[i]) * (x0 - x) / (x0 - x1)
    return ys[-1]  # not reached


_warned_curves: set[tuple[str, float]] = set()


def curve_points(curve: float, warn_key: str = "") -> tuple[float, ...] | None:
    """Flow temperatures of a curve at OUTDOOR_POINTS, or None if invalid.

    The 10 values of the official app come from the table. A value between
    them (not offered by the app) is interpolated between the two nearest
    curves as a fallback, with one warning per warn_key and value.
    """
    if curve is None or not CURVE_RANGE[0] - 1e-6 <= curve <= CURVE_RANGE[1] + 1e-6:
        return None
    for known, points in CURVE_TABLE.items():
        if abs(curve - known) < 1e-6:
            return points
    if (warn_key, curve) not in _warned_curves:
        _warned_curves.add((warn_key, curve))
        _LOGGER.warning(
            "climatic curve %s%s is not one of the values of the app: chart interpolated",
            curve, f" of thermostat {warn_key}" if warn_key else "",
        )
    lower = max(c for c in CURVES if c < curve)
    upper = min(c for c in CURVES if c > curve)
    ratio = (curve - lower) / (upper - lower)
    return tuple(
        a + (b - a) * ratio for a, b in zip(CURVE_TABLE[lower], CURVE_TABLE[upper])
    )


def curve_flow(curve: float, outdoor: float, warn_key: str = "") -> float | None:
    """Flow temperature of a curve at an outdoor temperature (not limited)."""
    points = curve_points(curve, warn_key)
    if points is None or outdoor is None:
        return None
    return _interp(outdoor, OUTDOOR_POINTS, points)


def limit_flow(flow: float | None, min_flow: float | None, max_flow: float | None) -> float | None:
    """Flow temperature limited between the min and max flow set points."""
    if flow is None:
        return None
    if max_flow is not None:
        flow = min(flow, max_flow)
    if min_flow is not None:
        flow = max(flow, min_flow)
    return flow


# ----------------------------------------------------------------- drawing


def flow_axis(values: ChartValues) -> tuple[float, float]:
    """PATCH 0.9 (b2): flow axis (bottom, top) in °C.

    Top: max(max + 15, 50) rounded up to a multiple of 10 (100 without a
    max). Bottom: 10 when the min or max flow is below 20 °C (the app allows
    min 10 and max 15), else 20. Unsupported unit: 20-100.
    """
    if values.status == STATUS_UNSUPPORTED_UNIT:
        return FLOW_RANGE
    top = FLOW_RANGE[1]
    if values.max_flow is not None:
        top = min(FLOW_RANGE[1], math.ceil(max(values.max_flow + FLOW_AXIS_MARGIN, FLOW_AXIS_MIN_TOP) / 10) * 10)
    low = [v for v in (values.min_flow, values.max_flow) if v is not None]
    bottom = FLOW_AXIS_LOW if low and min(low) < FLOW_RANGE[0] else FLOW_RANGE[0]
    return float(bottom), float(top)


def outdoor_off_scale(outdoor: float | None) -> bool:
    """Outdoor temperature outside the x axis (above 20 or below -16 °C)."""
    return outdoor is not None and not OUTDOOR_RANGE[1] <= outdoor <= OUTDOOR_RANGE[0]


def requested_off_scale(values: ChartValues) -> bool:
    """Requested flow outside the flow axis."""
    if values.requested is None:
        return False
    bottom, top = flow_axis(values)
    return not bottom <= values.requested <= top


WIDTH, HEIGHT = 480, 316
PLOT_LEFT, PLOT_RIGHT = 36.0, 444.0
PLOT_TOP, PLOT_BOTTOM = 26.0, 210.0
_SAMPLE_STEP = 0.5  # °C between the points of a drawn curve
_LABEL_GAP = 10.0  # px between curve labels
_LEGEND_TOP = 240.0
_LEGEND_ROW = 15.0

_STYLE = (
    ".bg{fill:#f6f7f9;stroke:#d0d4da}"
    ".grid{stroke:#e3e6ea;stroke-width:1}"
    ".axis{fill:#5c636d;font-size:9px}"
    ".txt{fill:#1f2328;font-size:10px}"
    ".note{fill:#5c636d;font-size:8.5px}"
    ".curve{fill:none;stroke:#bcc2ca;stroke-width:1.2}"
    ".clabel{fill:#8a9099;font-size:9px}"
    ".sel{fill:none;stroke:#e8590c;stroke-width:3;stroke-linejoin:round}"
    ".selfree{fill:none;stroke:#e8590c;stroke-width:1.2;stroke-dasharray:4 3;opacity:.45}"
    ".max{stroke:#d63939;stroke-width:1.6}"
    ".min{stroke:#2f7ed8;stroke-width:1.6}"
    ".work{fill:#e8590c;stroke:#fff;stroke-width:1.5}"
    ".workoff{fill:none;stroke:#e8590c;stroke-width:2}"
    ".req{fill:none;stroke:#8e44ad;stroke-width:2.2}"
    ".reqline{stroke:#8e44ad;stroke-width:1.2;stroke-dasharray:3 2}"
    ".offscale{fill:#1f2328}"
    ".msg{fill:#1f2328;font-size:13px;font-weight:bold}"
    "@media (prefers-color-scheme:dark){"
    ".bg{fill:#1e2126;stroke:#3a4048}"
    ".grid{stroke:#2e333a}"
    ".axis,.note{fill:#a3aab4}"
    ".txt,.msg,.offscale{fill:#e6e8eb}"
    ".curve{stroke:#59606a}"
    ".clabel{fill:#8b929c}"
    ".sel,.selfree,.workoff{stroke:#ffa94d}"
    ".max{stroke:#ff6b6b}"
    ".min{stroke:#5aa9ff}"
    ".work{fill:#ffa94d;stroke:#1e2126}"
    ".req,.reqline{stroke:#c39bd3}"
    "}"
)


def _esc(text) -> str:
    """XML escape of every text and attribute value put into the SVG."""
    return escape(str(text), {'"': "&quot;", "'": "&apos;"})


def _num(value: float) -> str:
    """35.0 -> "35", 15.3 -> "15.3" (one decimal at most)."""
    text = f"{value:.1f}"
    return text[:-2] if text.endswith(".0") else text


def _x(outdoor: float) -> float:
    """x of an outdoor temperature, held at the edges of the axis."""
    span = OUTDOOR_RANGE[0] - OUTDOOR_RANGE[1]
    outdoor = min(max(outdoor, OUTDOOR_RANGE[1]), OUTDOOR_RANGE[0])
    return PLOT_LEFT + (OUTDOOR_RANGE[0] - outdoor) * (PLOT_RIGHT - PLOT_LEFT) / span


def _y(flow: float, axis: tuple[float, float]) -> float:
    """y of a flow temperature, held at the edges of the axis."""
    bottom, top = axis
    flow = min(max(flow, bottom), top)
    return PLOT_BOTTOM - (flow - bottom) * (PLOT_BOTTOM - PLOT_TOP) / (top - bottom)


def _text(x: float, y: float, content, css: str, anchor: str = "start") -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" class="{_esc(css)}" '
        f'text-anchor="{_esc(anchor)}">{_esc(content)}</text>'
    )


def _polyline(points: list[tuple[float, float]], css: str, axis) -> str:
    coords = " ".join(f"{_x(t):.1f},{_y(f, axis):.1f}" for t, f in points)
    return f'<polyline class="{_esc(css)}" points="{_esc(coords)}" clip-path="url(#plot)"/>'


def _triangle(x: float, y: float, direction: str) -> str:
    """"Off scale" mark: a small triangle pointing left/right/up/down."""
    tip, base, half = 6.5, 13.0, 4.0
    shapes = {
        "left": ((x + tip, y), (x + base, y - half), (x + base, y + half)),
        "right": ((x - tip, y), (x - base, y - half), (x - base, y + half)),
        "up": ((x, y + tip), (x - half, y + base), (x + half, y + base)),
        "down": ((x, y - tip), (x - half, y - base), (x + half, y - base)),
    }
    coords = " ".join(f"{px:.1f},{py:.1f}" for px, py in shapes[direction])
    return f'<polygon class="offscale" points="{_esc(coords)}"/>'


def _samples(points: tuple[float, ...]) -> list[tuple[float, float]]:
    """(outdoor, flow) every _SAMPLE_STEP °C along a curve."""
    steps = round((OUTDOOR_RANGE[0] - OUTDOOR_RANGE[1]) / _SAMPLE_STEP)
    result = []
    for i in range(steps + 1):
        outdoor = OUTDOOR_RANGE[0] - i * _SAMPLE_STEP
        result.append((outdoor, _interp(outdoor, OUTDOOR_POINTS, points)))
    return result


def _visible(samples: list[tuple[float, float]], top: float) -> list[tuple[float, float]]:
    """Cut a curve where it leaves the chart at the top."""
    result = []
    for i, (outdoor, flow) in enumerate(samples):
        if flow <= top:
            result.append((outdoor, flow))
            continue
        if i and samples[i - 1][1] < top:
            prev_t, prev_f = samples[i - 1]
            result.append((prev_t + (outdoor - prev_t) * (top - prev_f) / (flow - prev_f), top))
        break
    return result


def _grid(axis: tuple[float, float]) -> list[str]:
    bottom, top = axis
    step = 5 if top - bottom <= 40 else 10
    parts = []
    for flow in range(int(bottom), int(top) + 1, step):
        y = _y(flow, axis)
        parts.append(f'<line class="grid" x1="{PLOT_LEFT:.1f}" y1="{y:.1f}" x2="{PLOT_RIGHT:.1f}" y2="{y:.1f}"/>')
        parts.append(_text(PLOT_LEFT - 4, y + 3, flow, "axis", "end"))
    for outdoor in range(int(OUTDOOR_RANGE[0]), int(OUTDOOR_RANGE[1]) - 1, -4):
        x = _x(outdoor)
        parts.append(f'<line class="grid" x1="{x:.1f}" y1="{PLOT_TOP:.1f}" x2="{x:.1f}" y2="{PLOT_BOTTOM:.1f}"/>')
        anchor = "start" if outdoor == OUTDOOR_RANGE[0] else "middle"
        parts.append(_text(x, PLOT_BOTTOM + 12, outdoor, "axis", anchor))
    return parts


def _curve_labels(ends: list[tuple[float, float, str]], axis) -> list[str]:
    """Labels of the grey curves: above the top edge where a curve leaves
    the chart (a second row when two would overlap), else on the right."""
    top_labels = sorted((_x(t), label) for t, f, label in ends if f >= axis[1] - 1e-6)
    right_labels = sorted((_y(f, axis), label) for t, f, label in ends if f < axis[1] - 1e-6)
    parts = []
    last_x = {0: -1e9, 1: -1e9}
    for x, label in top_labels:
        width = 5.5 * len(label)
        row = 0 if x - width / 2 >= last_x[0] + 4 else 1
        last_x[row] = x + width / 2
        parts.append(_text(x, PLOT_TOP - 3 - row * 9, label, "clabel", "middle"))
    last_y = -1e9
    for y, label in right_labels:
        y = max(y, last_y + _LABEL_GAP)
        last_y = y
        parts.append(_text(PLOT_RIGHT + 3, min(y, PLOT_BOTTOM) + 3, label, "clabel"))
    return parts


def legend_outdoor(values: ChartValues, texts: dict, work: float | None, flow: float | None) -> str:
    """Working point: real outdoor temperature, curve value, limit if any."""
    text = f"{texts['outdoor']} {_num(values.outdoor)} °C"
    if outdoor_off_scale(values.outdoor):
        edge = OUTDOOR_RANGE[0] if values.outdoor > OUTDOOR_RANGE[0] else OUTDOOR_RANGE[1]
        text += f" ({texts['off_scale']}, {texts['value_at']} {_num(edge)} °C)"
    if flow is not None:
        text += f" → {texts['curve_flow']} {_num(flow)} °C"
        if work is not None and _num(work) != _num(flow):
            text += f", {texts['limited_to']} {_num(work)} °C"
    return text


def render_svg(values: ChartValues, warn_key: str = "") -> str:
    """Draw the chart. Missing values are simply not drawn (never a 0 line);
    nothing is drawn outside the image, values off the axes are held at the
    edge with an "off scale" mark."""
    texts = TEXTS.get(values.language, TEXTS["en"])
    axis = flow_axis(values)
    plot_w, plot_h = PLOT_RIGHT - PLOT_LEFT, PLOT_BOTTOM - PLOT_TOP
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" font-family="sans-serif">',
        f"<style>{_STYLE}</style>",
        # The clip area is 2 px larger, so lines on the edges (e.g. min
        # 10 °C on an axis from 10) stay visible.
        f'<defs><clipPath id="plot"><rect x="{PLOT_LEFT - 2:.1f}" y="{PLOT_TOP - 2:.1f}" '
        f'width="{plot_w + 4:.1f}" height="{plot_h + 4:.1f}"/></clipPath></defs>',
        f'<rect class="bg" x="0.5" y="0.5" width="{WIDTH - 1}" height="{HEIGHT - 1}" rx="10"/>',
        *_grid(axis),
        _text(4, 10, texts["flow_axis"], "axis", "start"),
        _text(PLOT_RIGHT, PLOT_BOTTOM + 24, texts["outdoor_axis"], "axis", "end"),
    ]
    # Legend rows: (column, row, mark, text); columns 0 and 1 of the 2x2
    # grid, column None = full width.
    legend: list[tuple[int | None, int, str | None, str]] = []

    def entry(label: str, value: str | None) -> str:
        return f"{label} {value}" if value is not None else f"{label}: {texts['not_available']}"

    if values.status == STATUS_UNSUPPORTED_UNIT:
        mid_x = (PLOT_LEFT + PLOT_RIGHT) / 2
        parts.append(_text(mid_x, (PLOT_TOP + PLOT_BOTTOM) / 2, texts["unsupported_unit"], "msg", "middle"))
        legend.append((None, 0, None, texts["unsupported_unit"]))
    else:
        # All the curves of the manual, light grey, with their label.
        ends = []
        for curve, points in CURVE_TABLE.items():
            visible = _visible(_samples(points), axis[1])
            parts.append(_polyline(visible, "curve", axis))
            ends.append((*visible[-1], _num(curve)))
        parts.extend(_curve_labels(ends, axis))

        # Max / min flow set points: horizontal lines (only valid values,
        # always inside the axis by construction).
        for flow, css in ((values.max_flow, "max"), (values.min_flow, "min")):
            if flow is not None:
                y = _y(flow, axis)
                parts.append(
                    f'<line class="{css}" x1="{PLOT_LEFT:.1f}" y1="{y:.1f}" '
                    f'x2="{PLOT_RIGHT:.1f}" y2="{y:.1f}" clip-path="url(#plot)"/>'
                )

        # Selected curve: free (dashed) and limited between min and max.
        points = curve_points(values.curve, warn_key) if values.curve is not None else None
        if points is not None:
            samples = _samples(points)
            parts.append(_polyline(_visible(samples, axis[1]), "selfree", axis))
            limited = [(t, limit_flow(f, values.min_flow, values.max_flow)) for t, f in samples]
            parts.append(_polyline(_visible(limited, axis[1]), "sel", axis))

        legend.append((0, 0, "sel", entry(texts["curve"], _num(values.curve) if values.curve is not None else None)))
        legend.append((1, 0, None, entry(texts["influence"], _num(values.influence) if values.influence is not None else None)))
        legend.append((0, 1, "max", entry(texts["max"], f"{_num(values.max_flow)} °C" if values.max_flow is not None else None)))
        legend.append((1, 1, "min", entry(texts["min"], f"{_num(values.min_flow)} °C" if values.min_flow is not None else None)))

        if values.status == STATUS_BOILER_OFFLINE:
            legend.append((None, 2, None, f"{texts['outdoor']}: {texts['boiler_offline']}"))
            legend.append((None, 3, None, f"{texts['requested']}: {texts['boiler_offline']}"))
        else:
            work = flow = None
            outdoor_off = outdoor_off_scale(values.outdoor)
            if values.outdoor is None:
                legend.append((None, 2, "work", entry(texts["outdoor"], None)))
            else:
                # Off the axis: the value at the edge of the table, never
                # extrapolated (_interp holds the table ends).
                flow = curve_flow(values.curve, values.outdoor, warn_key) if points is not None else None
                work = limit_flow(flow, values.min_flow, values.max_flow)
                legend.append((None, 2, "workoff" if outdoor_off else "work",
                               legend_outdoor(values, texts, work, flow)))
            requested_off = requested_off_scale(values)
            if values.requested is not None:
                source = texts.get(f"source_{values.requested_source}")
                text = f"{texts['requested']} {_num(values.requested)} °C"
                if requested_off:
                    text += f", {texts['off_scale']}"
                legend.append((None, 3, "req", f"{text} ({source})" if source else text))
            elif values.heat_request:
                legend.append((None, 3, "req", entry(texts["requested"], None)))
            else:
                legend.append((None, 3, None, f"{texts['requested']}: {texts['no_heat_request']}"))

            if values.outdoor is not None:
                x = _x(values.outdoor)
                if values.requested is not None:
                    y_req = _y(values.requested, axis)
                    if work is not None:
                        parts.append(
                            f'<line class="reqline" x1="{x:.1f}" y1="{_y(work, axis):.1f}" '
                            f'x2="{x:.1f}" y2="{y_req:.1f}"/>'
                        )
                    parts.append(f'<circle class="req" cx="{x:.1f}" cy="{y_req:.1f}" r="5"/>')
                    if requested_off:
                        parts.append(_triangle(x, y_req, "up" if values.requested > axis[1] else "down"))
                if work is not None:
                    y_work = _y(work, axis)
                    css = "workoff" if outdoor_off else "work"
                    parts.append(f'<circle class="{css}" cx="{x:.1f}" cy="{y_work:.1f}" r="4.5"/>')
                    if outdoor_off:
                        parts.append(_triangle(x, y_work, "left" if values.outdoor > OUTDOOR_RANGE[0] else "right"))
                elif outdoor_off:
                    parts.append(_triangle(x, (PLOT_TOP + PLOT_BOTTOM) / 2,
                                           "left" if values.outdoor > OUTDOOR_RANGE[0] else "right"))

    # Legend: a 2x2 grid for the settings, then full-width rows for the
    # working point and the requested flow; a colour mark before each entry.
    marks = {
        "sel": '<line class="sel mark" x1="{x}" y1="{y}" x2="{x2}" y2="{y}"/>',
        "max": '<line class="max mark" x1="{x}" y1="{y}" x2="{x2}" y2="{y}"/>',
        "min": '<line class="min mark" x1="{x}" y1="{y}" x2="{x2}" y2="{y}"/>',
        "work": '<circle class="work mark" cx="{cx}" cy="{y}" r="4"/>',
        "workoff": '<circle class="workoff mark" cx="{cx}" cy="{y}" r="3.5"/>',
        "req": '<circle class="req mark" cx="{cx}" cy="{y}" r="4"/>',
    }
    for column, row, mark, line in legend:
        x = 14 + (column or 0) * 232
        y = _LEGEND_TOP + row * _LEGEND_ROW
        if mark is not None:
            parts.append(marks[mark].format(x=x, x2=x + 14, cx=x + 7, y=y - 3.5))
        parts.append(_text(x + 20, y, line, "txt"))
    parts.append(_text(14, HEIGHT - 9, texts["note"], "note"))
    parts.append("</svg>")
    return "".join(parts)

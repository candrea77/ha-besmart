"""PATCH 0.9: climatic curve chart (SVG), pure Python.

Curves of the Riello manual (heating flow temperature as a function of the
outdoor temperature), traced from the manual's chart: one point every 4 °C
from 20 to -16 °C, linear interpolation in between. The chart is indicative,
not the boiler's own calculation. No Home Assistant import here: the image
entity (image.py) collects the values, this module only computes and draws.
"""

from __future__ import annotations

import logging
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

# Chart axes.
OUTDOOR_RANGE = (20.0, -16.0)
FLOW_RANGE = (20.0, 100.0)
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

WIDTH, HEIGHT = 480, 316
PLOT_LEFT, PLOT_RIGHT = 36.0, 444.0
PLOT_TOP, PLOT_BOTTOM = 20.0, 216.0
_SAMPLE_STEP = 0.5  # °C between the points of a drawn curve

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
    ".sellabel{fill:#e8590c;font-size:10px;font-weight:bold}"
    ".max{stroke:#d63939;stroke-width:1.6}"
    ".min{stroke:#2f7ed8;stroke-width:1.6}"
    ".work{fill:#e8590c;stroke:#fff;stroke-width:1.5}"
    ".req{fill:none;stroke:#8e44ad;stroke-width:2.2}"
    ".reqline{stroke:#8e44ad;stroke-width:1.2;stroke-dasharray:3 2}"
    ".msg{fill:#1f2328;font-size:13px;font-weight:bold}"
    "@media (prefers-color-scheme:dark){"
    ".bg{fill:#1e2126;stroke:#3a4048}"
    ".grid{stroke:#2e333a}"
    ".axis,.note{fill:#a3aab4}"
    ".txt,.msg{fill:#e6e8eb}"
    ".curve{stroke:#59606a}"
    ".clabel{fill:#8b929c}"
    ".sel,.selfree{stroke:#ffa94d}"
    ".sellabel{fill:#ffa94d}"
    ".max{stroke:#ff6b6b}"
    ".min{stroke:#5aa9ff}"
    ".work{fill:#ffa94d;stroke:#1e2126}"
    ".req{stroke:#c39bd3}"
    ".reqline{stroke:#c39bd3}"
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
    span = OUTDOOR_RANGE[0] - OUTDOOR_RANGE[1]
    outdoor = min(max(outdoor, OUTDOOR_RANGE[1]), OUTDOOR_RANGE[0])
    return PLOT_LEFT + (OUTDOOR_RANGE[0] - outdoor) * (PLOT_RIGHT - PLOT_LEFT) / span


def _y(flow: float) -> float:
    span = FLOW_RANGE[1] - FLOW_RANGE[0]
    return PLOT_BOTTOM - (flow - FLOW_RANGE[0]) * (PLOT_BOTTOM - PLOT_TOP) / span


def _text(x: float, y: float, content, css: str, anchor: str = "start") -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" class="{_esc(css)}" '
        f'text-anchor="{_esc(anchor)}">{_esc(content)}</text>'
    )


def _polyline(points: list[tuple[float, float]], css: str) -> str:
    coords = " ".join(f"{_x(t):.1f},{_y(f):.1f}" for t, f in points)
    return f'<polyline class="{_esc(css)}" points="{_esc(coords)}"/>'


def _samples(points: tuple[float, ...]) -> list[tuple[float, float]]:
    """(outdoor, flow) every _SAMPLE_STEP °C along a curve."""
    steps = round((OUTDOOR_RANGE[0] - OUTDOOR_RANGE[1]) / _SAMPLE_STEP)
    result = []
    for i in range(steps + 1):
        outdoor = OUTDOOR_RANGE[0] - i * _SAMPLE_STEP
        result.append((outdoor, _interp(outdoor, OUTDOOR_POINTS, points)))
    return result


def _visible(samples: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Cut a curve where it leaves the chart (100 °C)."""
    top = FLOW_RANGE[1]
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


def _grid() -> list[str]:
    parts = []
    for flow in range(int(FLOW_RANGE[0]), int(FLOW_RANGE[1]) + 1, 10):
        y = _y(flow)
        parts.append(f'<line class="grid" x1="{PLOT_LEFT:.1f}" y1="{y:.1f}" x2="{PLOT_RIGHT:.1f}" y2="{y:.1f}"/>')
        parts.append(_text(PLOT_LEFT - 4, y + 3, flow, "axis", "end"))
    for outdoor in range(int(OUTDOOR_RANGE[0]), int(OUTDOOR_RANGE[1]) - 1, -4):
        x = _x(outdoor)
        parts.append(f'<line class="grid" x1="{x:.1f}" y1="{PLOT_TOP:.1f}" x2="{x:.1f}" y2="{PLOT_BOTTOM:.1f}"/>')
        anchor = "start" if outdoor == OUTDOOR_RANGE[0] else "middle"
        parts.append(_text(x, PLOT_BOTTOM + 12, outdoor, "axis", anchor))
    return parts


def render_svg(values: ChartValues, warn_key: str = "") -> str:
    """Draw the chart. Missing values are simply not drawn (never a 0 line)."""
    texts = TEXTS.get(values.language, TEXTS["en"])
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" font-family="sans-serif">',
        f"<style>{_STYLE}</style>",
        f'<rect class="bg" x="0.5" y="0.5" width="{WIDTH - 1}" height="{HEIGHT - 1}" rx="10"/>',
        *_grid(),
        _text(PLOT_LEFT - 4, PLOT_TOP - 8, texts["flow_axis"], "axis", "start"),
        _text(PLOT_RIGHT, PLOT_BOTTOM + 24, texts["outdoor_axis"], "axis", "end"),
    ]
    legend: list[str] = []

    def entry(label: str, value: str | None) -> str:
        return f"{label} {value}" if value is not None else f"{label}: {texts['not_available']}"

    if values.status == STATUS_UNSUPPORTED_UNIT:
        mid_x = (PLOT_LEFT + PLOT_RIGHT) / 2
        parts.append(_text(mid_x, (PLOT_TOP + PLOT_BOTTOM) / 2, texts["unsupported_unit"], "msg", "middle"))
        legend.append(texts["unsupported_unit"])
    else:
        # All the curves of the manual, light grey, with their label.
        for curve, points in CURVE_TABLE.items():
            visible = _visible(_samples(points))
            parts.append(_polyline(visible, "curve"))
            end_t, end_f = visible[-1]
            if end_f >= FLOW_RANGE[1] - 1e-6:
                parts.append(_text(_x(end_t), PLOT_TOP - 3, _num(curve), "clabel", "middle"))
            else:
                parts.append(_text(PLOT_RIGHT + 3, _y(end_f) + 3, _num(curve), "clabel"))

        # Max / min flow set points: horizontal lines (only valid values).
        for flow, css in ((values.max_flow, "max"), (values.min_flow, "min")):
            if flow is not None and FLOW_RANGE[0] <= flow <= FLOW_RANGE[1]:
                y = _y(flow)
                parts.append(f'<line class="{css}" x1="{PLOT_LEFT:.1f}" y1="{y:.1f}" x2="{PLOT_RIGHT:.1f}" y2="{y:.1f}"/>')

        # Selected curve: free (dashed) and limited between min and max.
        points = curve_points(values.curve, warn_key) if values.curve is not None else None
        if points is not None:
            samples = _samples(points)
            parts.append(_polyline(_visible(samples), "selfree"))
            limited = [(t, limit_flow(f, values.min_flow, values.max_flow)) for t, f in samples]
            parts.append(_polyline(_visible(limited), "sel"))

        legend.append(entry(texts["curve"], _num(values.curve) if values.curve is not None else None))
        legend.append(entry(texts["max"], f"{_num(values.max_flow)} °C" if values.max_flow is not None else None))
        legend.append(entry(texts["min"], f"{_num(values.min_flow)} °C" if values.min_flow is not None else None))
        legend.append(entry(texts["influence"], _num(values.influence) if values.influence is not None else None))

        if values.status == STATUS_BOILER_OFFLINE:
            legend.append(f"{texts['outdoor']}: {texts['boiler_offline']}")
            legend.append(f"{texts['requested']}: {texts['boiler_offline']}")
        else:
            work = None
            if values.outdoor is None:
                legend.append(entry(texts["outdoor"], None))
            else:
                flow = curve_flow(values.curve, values.outdoor, warn_key) if points is not None else None
                work = limit_flow(flow, values.min_flow, values.max_flow)
                text = f"{texts['outdoor']} {_num(values.outdoor)} °C"
                if work is not None:
                    text += f" → {texts['curve_flow']} {_num(work)} °C"
                legend.append(text)
            if values.requested is not None:
                source = texts.get(f"source_{values.requested_source}")
                text = f"{texts['requested']} {_num(values.requested)} °C"
                legend.append(f"{text} ({source})" if source else text)
            elif values.heat_request:
                legend.append(entry(texts["requested"], None))
            else:
                legend.append(f"{texts['requested']}: {texts['no_heat_request']}")

            if values.outdoor is not None:
                x = _x(values.outdoor)
                if values.requested is not None:
                    if work is not None:
                        parts.append(
                            f'<line class="reqline" x1="{x:.1f}" y1="{_y(work):.1f}" '
                            f'x2="{x:.1f}" y2="{_y(min(values.requested, FLOW_RANGE[1])):.1f}"/>'
                        )
                    parts.append(
                        f'<circle class="req" cx="{x:.1f}" '
                        f'cy="{_y(min(values.requested, FLOW_RANGE[1])):.1f}" r="5"/>'
                    )
                if work is not None:
                    parts.append(f'<circle class="work" cx="{x:.1f}" cy="{_y(work):.1f}" r="4.5"/>')

    # Legend: settings on the left (4 rows), outdoor temperature and
    # requested flow on the right; a colour mark before each entry.
    marks = {
        0: '<line class="sel mark" x1="{x}" y1="{y}" x2="{x2}" y2="{y}"/>',
        1: '<line class="max mark" x1="{x}" y1="{y}" x2="{x2}" y2="{y}"/>',
        2: '<line class="min mark" x1="{x}" y1="{y}" x2="{x2}" y2="{y}"/>',
        4: '<circle class="work mark" cx="{cx}" cy="{y}" r="4"/>',
        5: '<circle class="req mark" cx="{cx}" cy="{y}" r="4"/>',
    }
    for i, line in enumerate(legend):
        column, row = (0, i) if i < 4 else (1, i - 4)
        x = 14 + column * 186
        y = 248 + row * 15
        if values.status != STATUS_UNSUPPORTED_UNIT and i in marks:
            parts.append(marks[i].format(x=x, x2=x + 14, cx=x + 7, y=y - 3.5))
        parts.append(_text(x + 20, y, line, "txt"))
    parts.append(_text(14, HEIGHT - 9, texts["note"], "note"))
    parts.append("</svg>")
    return "".join(parts)

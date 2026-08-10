"""RetailPulse design system.

Three things live here, and nothing else should:

1. **Tokens** — the palette, in both modes, as Python. These mirror
   `.streamlit/config.toml`; that file styles the widgets, this one styles the
   charts, and they have to agree or the page comes apart.
2. **A registered Altair theme** — so every chart inherits the same ink, grid
   and typography instead of each chart restating them.
3. **A thin CSS layer plus a few HTML components** — only for what Streamlit's
   theming API genuinely can't express.

On the palette: these are the validated data-viz reference values — categorical
slot 1 (blue) and slot 2 (orange) on warm near-white / near-black surfaces.
Both modes pass all six checks (lightness band, chroma floor, CVD separation,
normal-vision floor, contrast vs. surface) against their own surface. Slots 1
and 2 are the only two ever on screen together, and their worst-case CVD
separation is ΔE 24.7 light / 26.8 dark against a target of 8. **Re-run
`validate_palette.js` before changing any hex here.**

On the CSS: selectors target `data-testid` attributes, which Streamlit treats
as a public-ish contract, rather than the generated emotion class names, which
change between releases. It is still the most fragile part of the dashboard —
if a Streamlit upgrade makes the page look wrong, look here first.
"""

from __future__ import annotations

from dataclasses import dataclass

import altair as alt
import streamlit as st


@dataclass(frozen=True)
class Palette:
    """Every colour the charts use, for one mode."""

    surface: str  # card / chart surface
    plane: str  # page background behind the cards
    ink: str  # primary text
    ink_secondary: str
    ink_muted: str  # axis labels, de-emphasised text
    grid: str  # hairline gridline
    axis: str  # baseline / domain line
    series_1: str  # categorical slot 1 — the default single series
    series_2: str  # categorical slot 2 — second series (projections)
    good: str
    critical: str
    warning: str


DARK = Palette(
    surface="#1a1a19",
    plane="#0d0d0d",
    ink="#ffffff",
    ink_secondary="#c3c2b7",
    ink_muted="#898781",
    grid="#2c2c2a",
    axis="#383835",
    series_1="#3987e5",
    series_2="#d95926",
    good="#0ca30c",
    critical="#d03b3b",
    warning="#fab219",
)

LIGHT = Palette(
    surface="#fcfcfb",
    plane="#f9f9f7",
    ink="#0b0b0b",
    ink_secondary="#52514e",
    ink_muted="#898781",
    grid="#e1e0d9",
    axis="#c3c2b7",
    series_1="#2a78d6",
    series_2="#eb6834",
    good="#006300",
    critical="#d03b3b",
    warning="#fab219",
)

# IBM Plex Sans rather than the system stack. Two reasons beyond taste: its
# figures are genuinely tabular, which matters on a page that is almost entirely
# numbers in columns, and it holds up at the 11px axis-label sizes the charts
# use where a system UI font goes muddy. The fallbacks are the previous stack,
# so an offline load degrades to exactly what this looked like before.
FONT = "'IBM Plex Sans', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"

# Reserved for the wordmark alone. A serif in one deliberate place reads as
# brand; a serif in the section headings of a dense dashboard reads as a
# scanability problem.
DISPLAY_FONT = "'Instrument Serif', Georgia, 'Times New Roman', serif"

FONT_IMPORT = (
    "@import url('https://fonts.googleapis.com/css2?"
    "family=IBM+Plex+Sans:wght@400;500;600;700&family=Instrument+Serif&display=swap');"
)

# Mark specs, from the data-viz reference. Named here so no chart restates them.
BAR_MAX_WIDTH = 24
LINE_WIDTH = 2
POINT_SIZE = 64  # Vega area units ≈ 8px diameter
CORNER_RADIUS = 4
AREA_OPACITY = 0.10


def _is_light(hex_color: str) -> bool:
    """Rough relative luminance of a #rrggbb string."""
    value = hex_color.lstrip("#")
    r, g, b = (int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) > 0.5


def palette() -> Palette:
    """The palette matching the chrome Streamlit actually rendered.

    Derived from the resolved `theme.backgroundColor` rather than from
    `st.context.theme.type`. That property reports the viewer's OS preference,
    which is only the same thing as the app's appearance while the config
    defines both a light and a dark theme. It no longer does — config.toml pins
    the app to light — so on a dark desktop the two disagreed: the page
    rendered light while the charts and every CSS rule here were handed the
    dark palette. Dark cards on a white page, and a title that vanished into
    its own background.

    Reading the background colour keeps the rule the design system is built on:
    config.toml styles the widgets, this file styles the charts, and they agree
    because they are both looking at the same value.
    """
    try:
        background = st.get_option("theme.backgroundColor")
        if background:
            return LIGHT if _is_light(background) else DARK
    except Exception:  # pragma: no cover - option unavailable
        pass
    try:
        return LIGHT if st.context.theme.type == "light" else DARK
    except Exception:  # pragma: no cover - older Streamlit, or no context
        return LIGHT


def register_chart_theme() -> None:
    """Register (and enable) the Altair theme matching the app chrome.

    Streamlit's built-in chart theme is deliberately not used: it styles charts
    to Streamlit's own look, and the whole point here is that the charts belong
    to *this* design system. Every `st.altair_chart` call therefore passes
    `theme=None` so this one wins.
    """
    p = palette()

    @alt.theme.register("retailpulse", enable=True)
    def _theme() -> alt.theme.ThemeConfig:
        return {
            "config": {
                "background": "transparent",
                "font": FONT,
                "view": {"stroke": "transparent"},
                "axis": {
                    "labelFont": FONT,
                    "titleFont": FONT,
                    "labelFontSize": 11,
                    "titleFontSize": 11,
                    "labelColor": p.ink_muted,
                    "titleColor": p.ink_muted,
                    "titleFontWeight": "normal",
                    "titlePadding": 10,
                    # Hairline, solid, one step off surface — never dashed.
                    "gridColor": p.grid,
                    "gridWidth": 1,
                    "domainColor": p.axis,
                    "tickColor": p.axis,
                    "tickSize": 4,
                    "labelPadding": 6,
                },
                # Gridlines belong to the value axis only. Running them along
                # the category/time axis as well boxes the marks into a cage
                # and adds ink that carries no value.
                "axisX": {"grid": False},
                "axisY": {"grid": True, "tickSize": 0, "domain": False},
                "legend": {
                    "labelFont": FONT,
                    "titleFont": FONT,
                    "labelFontSize": 11,
                    "labelColor": p.ink_secondary,
                    "titleColor": p.ink_muted,
                    "symbolStrokeWidth": 2,
                    "symbolSize": 90,
                    "orient": "top",
                    "direction": "horizontal",
                    "offset": 8,
                },
                "range": {"category": [p.series_1, p.series_2]},
                "bar": {"color": p.series_1},
                "line": {"color": p.series_1, "strokeWidth": LINE_WIDTH},
                "point": {"color": p.series_1, "size": POINT_SIZE},
                "area": {"color": p.series_1, "opacity": AREA_OPACITY},
            }
        }

    _theme  # noqa: B018 - registered for its side effect


def _css(p: Palette) -> str:
    """The rules Streamlit's theming API can't reach."""
    return f"""
    <style>
      /* Must be the first rule in this stylesheet or the browser drops it. */
      {FONT_IMPORT}

      /* ---- page rhythm -------------------------------------------------- */
      /* Streamlit's default top padding assumes a page title; this app has a
         designed header instead, so the gap comes out. */
      [data-testid="stMainBlockContainer"] {{
          padding-top: 2.25rem;
          padding-bottom: 4rem;
          max-width: 1560px;
      }}
      [data-testid="stSidebarUserContent"] {{ padding-top: 1.5rem; }}
      [data-testid="stHeader"] {{ background: transparent; }}

      /* ---- app header --------------------------------------------------- */
      .rp-header {{
          display: flex; align-items: center; gap: 0.875rem;
          margin-bottom: 1.25rem;
      }}
      .rp-mark {{
          width: 38px; height: 38px; border-radius: 10px; flex: 0 0 38px;
          background: linear-gradient(135deg, {p.series_1}, {p.series_2});
          display: grid; place-items: center;
      }}
      .rp-mark svg {{ display: block; }}
      .rp-titles {{ display: flex; flex-direction: column; gap: 1px; min-width: 0; }}
      .rp-title {{
          font-family: {DISPLAY_FONT};
          font-size: 1.75rem; font-weight: 400; letter-spacing: -0.005em;
          color: {p.ink}; line-height: 1.05;
      }}
      .rp-subtitle {{
          font-size: 0.8125rem; color: {p.ink_muted}; line-height: 1.3;
      }}
      .rp-header-spacer {{ flex: 1 1 auto; }}

      /* ---- status pill -------------------------------------------------- */
      .rp-pill {{
          display: inline-flex; align-items: center; gap: 0.4rem;
          padding: 0.3rem 0.7rem; border-radius: 999px;
          font-size: 0.75rem; font-weight: 550; white-space: nowrap;
          border: 1px solid {p.grid}; color: {p.ink_secondary};
          background: {p.surface};
      }}
      .rp-dot {{ width: 7px; height: 7px; border-radius: 50%; flex: 0 0 7px; }}

      /* ---- section headings --------------------------------------------- */
      /* st.subheader keeps carrying the sections, because its `help` argument
         is where the honest caveats live and a custom heading would lose the
         tooltip. It just gets tightened here. */
      [data-testid="stHeadingWithActionElements"] {{ margin-bottom: 0.6rem; }}
      [data-testid="stHeadingWithActionElements"] h3 {{ letter-spacing: -0.012em; }}

      /* ---- KPI cards ----------------------------------------------------- */
      /* Streamlit's bordered metric is close, but the label/value hierarchy is
         flat and the delta sits too loud. Restyle rather than rebuild, so the
         built-in sparkline keeps working. */
      [data-testid="stMetric"] {{
          background: {p.surface};
          border: 1px solid {p.grid};
          border-radius: 12px;
          /* Horizontal padding is deliberately tight: the widest value is a
             seven-figure sum shown to the cent, and every pixel here is a
             pixel that number doesn't have. */
          padding: 1rem 0.9rem 0.6rem 0.9rem;
          overflow: hidden;
      }}
      [data-testid="stMetricLabel"] p {{
          font-size: 0.75rem !important;
          font-weight: 550 !important;
          letter-spacing: 0.04em;
          text-transform: uppercase;
          color: {p.ink_muted} !important;
      }}
      [data-testid="stMetricValue"] {{
          color: {p.ink};
          letter-spacing: -0.025em;
          line-height: 1.1;
          /* Lining tabular figures so a column of KPI cards has its digits on
             the same vertical rhythm — proportional figures make $1,075.23 and
             $107,913.80 look like different type sizes. */
          font-variant-numeric: tabular-nums lining-nums;
          font-feature-settings: 'tnum' 1, 'lnum' 1;
      }}
      [data-testid="stMetricDelta"] {{
          font-size: 0.8125rem !important;
          font-weight: 550 !important;
          padding-top: 0.1rem;
      }}
      /* The built-in sparkline bleeds to the card edges instead of floating in
         the middle of the padding box. */
      [data-testid="stMetric"] [data-testid="stVegaLiteChart"],
      [data-testid="stMetric"] canvas {{
          margin: 0 -0.9rem -0.6rem -0.9rem !important;
          width: calc(100% + 1.8rem) !important;
      }}

      /* ---- tabs ---------------------------------------------------------- */
      /* Default tabs are a thin underline that reads as an afterthought. This
         makes them a proper segmented nav. */
      [data-testid="stTabs"] [role="tablist"] {{
          gap: 0.25rem;
          border-bottom: 1px solid {p.grid};
          margin-bottom: 1.25rem;
      }}
      [data-testid="stTabs"] [role="tab"] {{
          padding: 0.55rem 0.95rem;
          font-size: 0.875rem; font-weight: 550;
          color: {p.ink_muted};
          border-radius: 8px 8px 0 0;
      }}
      [data-testid="stTabs"] [role="tab"]:hover {{
          color: {p.ink}; background: {p.surface};
      }}
      [data-testid="stTabs"] [role="tab"][aria-selected="true"] {{ color: {p.ink}; }}

      /* ---- cards --------------------------------------------------------- */
      [data-testid="stVerticalBlockBorderWrapper"] {{ border-radius: 12px; }}

      /* ---- window control ------------------------------------------------ */
      /* The one control that drives every number on the page, so it stays put
         while the page scrolls: reaching for it used to mean scrolling back to
         the top from the bottom of a long item table.

         `.st-key-*` comes from `st.container(key=...)` and is a stable, public
         hook — unlike the generated emotion class names this file otherwise
         avoids. Sticky (not fixed) so it still participates in layout and
         cannot overlap the header above it.

         The negative margins and matching padding let the blurred background
         bleed to the container's edges; without them the content scrolling
         past would show through the gutters on either side. */
      /* The sticky rules go on the *wrapper* Streamlit puts around a keyed
         container, not on the container itself. A sticky box can only travel
         within its containing block, and that wrapper hugs the bar exactly
         (55px) — so styling the bar directly produces an element that is
         `position: sticky` and still scrolls away, which looks like the
         property silently not working. Promoting the wrapper gives it the full
         height of the page's vertical block to stick inside. */
      [data-testid="stLayoutWrapper"]:has(> .st-key-rp-window-bar) {{
          position: sticky;
          /* Clears Streamlit's own fixed toolbar. */
          top: 3.25rem;
          z-index: 100;
          /* No horizontal bleed. Widening this to cover the block container's
             padding does not work the way it looks like it should: the wrapper
             is a flex item, so an explicit `width` is ignored in favour of the
             stretch sizing, and a negative margin-left slides the bar without a
             negative margin-right widening it — which left a strip of chart
             visible past its right edge.
             It also isn't needed. Every element on the page lives inside the
             padded content box, so the gutters either side are empty; matching
             the content width exactly covers everything there is to cover. */
          margin: 0 0 0.25rem 0;
          padding: 0.6rem 0 0.5rem 0;
          border-bottom: 1px solid {p.grid};
          /* Fully opaque. A translucent bar with a backdrop blur looked right
             over flat areas and wrong over everything else: high-contrast bars
             and the daily-sales line ghosted straight through it on scroll,
             which reads as a rendering fault rather than as a design. There is
             nothing to gain from seeing the chart you are scrolling past
             through the control that filters it. */
          background: {p.plane};
          /* The seam the blur used to soften is now a shadow instead, which
             does the same job without letting anything show through. */
          box-shadow: 0 6px 16px -12px rgba(0, 0, 0, 0.5);
      }}
      /* Streamlit stacks a gap under every block; inside a bar this tight it
         reads as a stray line of empty space. */
      .st-key-rp-window-bar [data-testid="stHorizontalBlock"] {{ gap: 0.75rem; }}
      .st-key-rp-window-bar [data-testid="stElementContainer"] {{ margin-bottom: 0; }}
      .st-key-rp-window-bar [data-testid="stVerticalBlock"] {{ gap: 0.4rem; }}
      /* The resolved-window caption is the bar's second line: it names what is
         on screen, so it stays quiet but must not wrap into two lines. */
      .st-key-rp-window-bar [data-testid="stCaptionContainer"] {{
          font-size: 0.78125rem;
          line-height: 1.2;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
      }}

      [data-testid="stButtonGroup"] button {{
          font-size: 0.8125rem !important;
          font-weight: 550 !important;
      }}
      /* The date range sits beside the presets and should read as the same
         family of control, not as a form field that wandered in. */
      .st-key-rp-window-bar [data-testid="stDateInput"] input {{
          font-size: 0.8125rem;
          font-weight: 550;
      }}

      /* ---- tables -------------------------------------------------------- */
      [data-testid="stDataFrame"] {{ border-radius: 10px; }}

      /* ---- misc ---------------------------------------------------------- */
      [data-testid="stMainBlockContainer"] hr {{
          border-color: {p.grid}; margin: 1.5rem 0;
      }}
      /* Numbers that sit in columns line up; standalone display numbers keep
         the font's proportional figures. */
      [data-testid="stDataFrame"] {{ font-variant-numeric: tabular-nums; }}
    </style>
    """


def apply(page_title: str = "RetailPulse", page_icon: str = "📊") -> Palette:
    """Set up the page, styles and chart theme. Returns the active palette."""
    st.set_page_config(
        page_title=page_title,
        page_icon=page_icon,
        layout="wide",
        initial_sidebar_state="expanded",
    )
    p = palette()
    st.markdown(_css(p), unsafe_allow_html=True)
    register_chart_theme()
    return p


# --- components ------------------------------------------------------------


def header(title: str, subtitle: str, pill_label: str, pill_tone: str, p: Palette) -> None:
    """The app's own header: brand mark, title, and a freshness pill."""
    tone = {"good": p.good, "warning": p.warning, "critical": p.critical}.get(
        pill_tone, p.ink_muted
    )
    # An inline SVG rather than an emoji: emoji render differently per platform
    # and go muddy on a gradient, and this one is literally the product's name.
    mark = (
        '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" '
        'stroke="#ffffff" stroke-width="2.4" stroke-linecap="round" '
        'stroke-linejoin="round"><path d="M2 13h4l3-8 4 16 3-8h6"/></svg>'
    )
    st.markdown(
        f"""
        <div class="rp-header">
          <div class="rp-mark">{mark}</div>
          <div class="rp-titles">
            <div class="rp-title">{title}</div>
            <div class="rp-subtitle">{subtitle}</div>
          </div>
          <div class="rp-header-spacer"></div>
          <div class="rp-pill">
            <span class="rp-dot" style="background:{tone}"></span>{pill_label}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

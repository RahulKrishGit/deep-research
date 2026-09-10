"""Tests for the centralized Editorial Research Canvas visual foundation."""

from __future__ import annotations

import re

from deep_research.ui import styles


def test_style_tokens_match_approved_visual_handoff() -> None:
    assert styles.COLORS == {
        "background": "#FCFCFA",
        "surface": "#F5F7F6",
        "text": "#172126",
        "text_muted": "#66727A",
        "border": "#DCE2DF",
        "active": "#0F6F68",
        "active_tint": "#E8F3F1",
        "success": "#2F7A4D",
        "success_tint": "#EAF4ED",
        "warning": "#946200",
        "warning_tint": "#FFF4D6",
        "error": "#B42318",
        "error_tint": "#FDECEA",
    }
    assert styles.SPACING == {
        "1": "4px",
        "2": "8px",
        "3": "12px",
        "4": "16px",
        "6": "24px",
        "8": "32px",
        "10": "40px",
        "12": "48px",
    }
    assert styles.RADII == {"control": "6px", "container": "8px"}
    assert styles.BORDERS == {"default": "1px solid #DCE2DF"}
    assert styles.SHADOWS == {"default": "none"}
    assert styles.WIDTHS == {
        "sidebar": "240-270px",
        "details_rail": "220-260px",
        "report": "720-820px",
        "canvas": "1120px",
    }


def test_static_css_is_safe_and_stays_inside_approved_boundary() -> None:
    css = styles.STATIC_CSS

    assert "<script" not in css.casefold()
    assert "javascript:" not in css.casefold()
    assert "@keyframes" not in css.casefold()
    assert "animation" not in css.casefold()
    assert "@import" not in css.casefold()
    assert "http://" not in css.casefold()
    assert "https://" not in css.casefold()
    for color in styles.COLORS.values():
        assert color in css
    assert "max-width: 1120px" in css
    assert "font-family: Georgia" in css
    assert "box-shadow: none" in css
    assert "min-height: 44px" in css
    primary_rule = re.search(
        r'button\[kind="primary"\],\s*'
        r'button\[kind="primaryFormSubmit"\]\s*\{(?P<body>.*?)\}',
        css,
        flags=re.DOTALL,
    )
    assert primary_rule is not None
    assert "min-height: 44px !important;" in primary_rule.group("body")


def test_static_css_includes_native_chrome_and_responsive_top_padding() -> None:
    css = styles.STATIC_CSS

    assert '[data-testid="stAppViewContainer"] header' in css
    assert '[data-testid="stHeader"] {' in css
    assert "background: #FCFCFA;" in css
    assert "color: #172126;" in css
    assert "border-bottom: 1px solid #DCE2DF;" in css
    assert '[data-testid="stHeader"] button' in css
    assert '[data-testid="stHeader"] svg' in css
    assert "fill: #172126;" in css
    assert "opacity: 1;" in css

    main_rule = re.search(
        r'\[data-testid="stMainBlockContainer"\]\s*\{(?P<body>.*?)\}',
        css,
        flags=re.DOTALL,
    )
    assert main_rule is not None
    assert "padding-top: 64px;" in main_rule.group("body")
    assert '@media (max-width: 900px)' in css
    assert 'padding-top: 56px;' in css
    assert '@media (max-width: 640px)' in css
    assert 'padding-top: 48px;' in css


def test_static_css_includes_focus_disabled_readonly_and_spacing_rules() -> None:
    css = styles.STATIC_CSS

    assert "button:focus-visible" in css
    assert "a:focus-visible" in css
    assert "input:focus-visible" in css
    assert "textarea:focus-visible" in css
    assert '[role="radio"]:focus-visible' in css
    assert "outline: 3px solid #0F6F68;" in css
    assert "outline-offset: 2px;" in css
    assert 'button[kind="primary"]:focus-visible' in css
    assert "outline-color: #172126;" in css

    assert "input:disabled" in css
    assert "textarea:disabled" in css
    assert "button:disabled" in css
    assert "cursor: not-allowed;" in css

    readonly_rule = re.search(
        r"\.dr-readonly-field\s*\{(?P<body>.*?)\}", css, flags=re.DOTALL
    )
    assert readonly_rule is not None
    readonly_body = readonly_rule.group("body")
    assert "display: flex;" in readonly_body
    assert "align-items: center;" in readonly_body
    assert "justify-content: space-between;" in readonly_body
    assert "min-height: 44px;" in readonly_body
    assert "background: #F5F7F6;" in readonly_body

    assert ".dr-control-label" in css
    assert ".dr-readonly-meta" in css
    assert ".dr-screen-eyebrow" in css
    assert ".dr-subsection-heading" in css
    assert ".dr-next-steps" in css
    assert ".dr-next-steps > div" in css

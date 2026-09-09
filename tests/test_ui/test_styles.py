"""Tests for the centralized Editorial Research Canvas visual foundation."""

from __future__ import annotations

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

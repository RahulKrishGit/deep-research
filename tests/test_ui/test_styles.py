"""Tests for the centralized Editorial Research Canvas visual foundation."""

from __future__ import annotations

import re

from deep_research.ui import styles


def _contrast_ratio(foreground: str, background: str) -> float:
    def channel(value: str) -> float:
        normalized = int(value, 16) / 255
        return normalized / 12.92 if normalized <= 0.04045 else (
            (normalized + 0.055) / 1.055
        ) ** 2.4

    def luminance(color: str) -> float:
        red, green, blue = (
            channel(color[index : index + 2]) for index in (1, 3, 5)
        )
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue

    lighter = max(luminance(foreground), luminance(background))
    darker = min(luminance(foreground), luminance(background))
    return (lighter + 0.05) / (darker + 0.05)


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


def test_dark_style_tokens_match_approved_theme_contract() -> None:
    assert getattr(styles, "DARK_COLORS", None) == {
        "background": "#0E1117",
        "surface": "#171C22",
        "text": "#F2F4F3",
        "text_muted": "#A9B4BA",
        "border": "#5F6C73",
        "active": "#5CC8BE",
        "active_tint": "#163A37",
        "success": "#72D49A",
        "success_tint": "#173826",
        "warning": "#F2C66D",
        "warning_tint": "#3A2D12",
        "error": "#FF8A80",
        "error_tint": "#431F1F",
    }


def test_theme_tokens_meet_aa_contrast_on_intended_surfaces() -> None:
    dark_colors = getattr(styles, "DARK_COLORS", None)
    assert dark_colors is not None

    for colors in (styles.COLORS, dark_colors):
        assert _contrast_ratio(colors["text"], colors["background"]) >= 4.5
        assert _contrast_ratio(colors["text_muted"], colors["background"]) >= 4.5
        assert _contrast_ratio(colors["active"], colors["background"]) >= 4.5
        assert _contrast_ratio(colors["success"], colors["success_tint"]) >= 4.5
        assert _contrast_ratio(colors["warning"], colors["warning_tint"]) >= 4.5
        assert _contrast_ratio(colors["error"], colors["error_tint"]) >= 4.5
        assert _contrast_ratio(colors["background"], colors["active"]) >= 4.5


def test_static_css_defines_light_and_dark_theme_contracts() -> None:
    css = styles.STATIC_CSS

    assert ".stApp" in css and "color-scheme: light;" in css
    assert "@media (prefers-color-scheme: dark)" in css
    for token in (
        "--dr-background: #0E1117",
        "--dr-surface: #171C22",
        "--dr-text: #F2F4F3",
        "--dr-text-muted: #A9B4BA",
        "--dr-border: #5F6C73",
        "--dr-active: #5CC8BE",
        "--dr-on-active: #0E1117",
    ):
        assert token in css
    dark_block = css[css.index("@media (prefers-color-scheme: dark)") :]
    assert "color-scheme: dark;" in dark_block


def test_theme_sensitive_rules_use_semantic_variables() -> None:
    css = styles.STATIC_CSS

    for selector in (
        '[data-testid="stAppViewContainer"] header',
        ".dr-readonly-field",
        ".dr-control-label",
        ".dr-next-steps",
    ):
        assert selector in css
    assert "background: var(--dr-background);" in css
    assert "background: var(--dr-surface);" in css
    assert "color: var(--dr-text);" in css
    assert "border-bottom: 1px solid var(--dr-border);" in css


def test_button_states_have_project_owned_theme_rules() -> None:
    css = styles.STATIC_CSS

    for state in (":hover", ":active", ":focus-visible"):
        assert state in css
    assert "--dr-active-hover" in css
    assert "--dr-active-pressed" in css
    assert "--dr-disabled-bg" in css
    assert 'button[kind="secondary"]:hover' in css
    assert "button:disabled" in css


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
    assert "background: var(--dr-background);" in css
    assert "color: var(--dr-text);" in css
    assert "border-bottom: 1px solid var(--dr-border);" in css
    assert '[data-testid="stHeader"] button' in css
    assert '[data-testid="stHeader"] svg' in css
    assert "fill: var(--dr-text);" in css
    header_rule = re.search(
        r'\[data-testid="stHeader"\] button,\s*'
        r'\[data-testid="stHeader"\] svg\s*\{(?P<body>.*?)\}',
        css,
        flags=re.DOTALL,
    )
    assert header_rule is not None
    assert "opacity: 1;" in header_rule.group("body")

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


def test_static_css_stacks_new_research_form_columns_at_tablet_width() -> None:
    css = styles.STATIC_CSS
    tablet_start = css.index("@media (max-width: 900px)")
    narrow_start = css.index("@media (max-width: 640px)", tablet_start)
    tablet_css = css[tablet_start:narrow_start]

    form_row_rule = re.search(
        r'\[data-testid="stForm"\]\s*'
        r'\[data-testid="stHorizontalBlock"\]\s*\{(?P<body>.*?)\}',
        tablet_css,
        flags=re.DOTALL,
    )
    assert form_row_rule is not None
    assert "flex-wrap: wrap;" in form_row_rule.group("body")

    form_column_rule = re.search(
        r'\[data-testid="stForm"\]\s*'
        r'\[data-testid="stHorizontalBlock"\]\s*>\s*'
        r'\[data-testid="stColumn"\]\s*\{(?P<body>.*?)\}',
        tablet_css,
        flags=re.DOTALL,
    )
    assert form_column_rule is not None
    assert "min-width: 100% !important;" in form_column_rule.group("body")


def test_static_css_includes_focus_disabled_readonly_and_spacing_rules() -> None:
    css = styles.STATIC_CSS

    assert "button:focus-visible" in css
    assert "a:focus-visible" in css
    assert "input:focus-visible" in css
    assert "textarea:focus-visible" in css
    assert '[role="radio"]:focus-visible' in css
    assert "outline: 3px solid var(--dr-focus);" in css
    assert "outline-offset: 2px;" in css
    assert 'button[kind="primary"]:focus-visible' in css
    assert 'button[kind="primaryFormSubmit"]:focus-visible' in css
    primary_focus_rule = re.search(
        r'button\[kind="primary"\]:focus-visible,\s*'
        r'button\[kind="primaryFormSubmit"\]:focus-visible\s*'
        r'\{(?P<body>.*?)\}',
        css,
        flags=re.DOTALL,
    )
    assert primary_focus_rule is not None
    assert "outline-color: var(--dr-primary-focus);" in primary_focus_rule.group("body")

    assert "input:disabled" in css
    assert "textarea:disabled" in css
    assert "button:disabled" in css
    disabled_rule = re.search(
        r"input:disabled,\s*textarea:disabled,\s*button:disabled\s*"
        r"\{(?P<body>.*?)\}",
        css,
        flags=re.DOTALL,
    )
    assert disabled_rule is not None
    disabled_body = disabled_rule.group("body")
    assert "opacity: 1;" in disabled_body
    assert "cursor: not-allowed;" in disabled_body

    readonly_rule = re.search(
        r"\.dr-readonly-field\s*\{(?P<body>.*?)\}", css, flags=re.DOTALL
    )
    assert readonly_rule is not None
    readonly_body = readonly_rule.group("body")
    assert "display: flex;" in readonly_body
    assert "align-items: center;" in readonly_body
    assert "justify-content: space-between;" in readonly_body
    assert "min-height: 44px;" in readonly_body
    assert "background: var(--dr-surface);" in readonly_body

    assert ".dr-control-label" in css
    readonly_meta_rule = re.search(
        r"\.dr-readonly-meta\s*\{(?P<body>.*?)\}", css, flags=re.DOTALL
    )
    assert readonly_meta_rule is not None
    assert "color: var(--dr-text-muted);" in readonly_meta_rule.group("body")
    assert ".dr-screen-eyebrow" in css
    assert ".dr-subsection-heading" in css
    assert ".dr-next-steps" in css
    assert ".dr-next-steps > div" in css

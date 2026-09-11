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


def _rule_body(css: str, selector_pattern: str) -> str:
    match = re.search(
        rf"{selector_pattern}\s*\{{(?P<body>.*?)\}}",
        css,
        flags=re.DOTALL,
    )
    assert match is not None, f"Missing CSS rule matching {selector_pattern!r}"
    return match.group("body")


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

    header_body = _rule_body(
        css,
        r'\[data-testid="stAppViewContainer"\] header,\s*'
        r'\[data-testid="stHeader"\]',
    )
    readonly_body = _rule_body(css, r"\.dr-readonly-field")
    label_body = _rule_body(css, r"\.dr-control-label")
    next_steps_body = _rule_body(css, r"\.dr-next-steps")

    assert "background: var(--dr-background);" in header_body
    assert "color: var(--dr-text);" in header_body
    assert "border-bottom: 1px solid var(--dr-border);" in header_body
    assert "background: var(--dr-surface);" in readonly_body
    assert "border: 1px solid var(--dr-border);" in readonly_body
    assert "color: var(--dr-text);" in readonly_body
    assert "color: var(--dr-text);" in label_body
    assert "border-top: 1px solid var(--dr-border);" in next_steps_body

    for body in (header_body, readonly_body, label_body, next_steps_body):
        assert not any(color in body for color in styles.COLORS.values())


def test_running_subtopic_row_uses_semantic_theme_tokens() -> None:
    body = _rule_body(styles.STATIC_CSS, r"\.dr-subtopic-row--running")

    assert "background: var(--dr-active-tint);" in body
    assert "border-radius: var(--dr-radius-container);" in body
    assert "padding: var(--dr-space-3) var(--dr-space-2);" in body
    assert not any(color in body for color in styles.COLORS.values())


def test_button_states_have_project_owned_theme_rules() -> None:
    css = styles.STATIC_CSS

    contracts = (
        (
            "primary normal",
            r'button\[kind="primary"\],\s*'
            r'button\[kind="primaryFormSubmit"\]',
            (
                "background: var(--dr-active) !important;",
                "border-color: var(--dr-active) !important;",
                "color: var(--dr-on-active) !important;",
            ),
        ),
        (
            "primary hover",
            r'button\[kind="primary"\]:not\(:disabled\):hover,\s*'
            r'button\[kind="primaryFormSubmit"\]:not\(:disabled\):hover',
            (
                "background: var(--dr-active-hover) !important;",
                "border-color: var(--dr-active-hover) !important;",
            ),
        ),
        (
            "primary active",
            r'button\[kind="primary"\]:not\(:disabled\):active,\s*'
            r'button\[kind="primaryFormSubmit"\]:not\(:disabled\):active',
            (
                "background: var(--dr-active-pressed) !important;",
                "border-color: var(--dr-active-pressed) !important;",
            ),
        ),
        (
            "primary focus",
            r'button\[kind="primary"\]:not\(:disabled\):focus-visible,\s*'
            r'button\[kind="primaryFormSubmit"\]:not\(:disabled\):focus-visible',
            ("outline-color: var(--dr-primary-focus);",),
        ),
        (
            "primary disabled",
            r'button\[kind="primary"\]:disabled,\s*'
            r'button\[kind="primaryFormSubmit"\]:disabled',
            (
                "background: var(--dr-disabled-bg) !important;",
                "border-color: var(--dr-disabled-border) !important;",
                "color: var(--dr-disabled-text) !important;",
            ),
        ),
        (
            "secondary normal",
            r'button\[kind="secondary"\]',
            (
                "background: var(--dr-background) !important;",
                "border-color: var(--dr-border) !important;",
                "color: var(--dr-text) !important;",
            ),
        ),
        (
            "secondary hover",
            r'button\[kind="secondary"\]:not\(:disabled\):hover',
            (
                "background: var(--dr-surface) !important;",
                "border-color: var(--dr-active) !important;",
            ),
        ),
        (
            "secondary active",
            r'button\[kind="secondary"\]:not\(:disabled\):active',
            (
                "background: var(--dr-active-tint) !important;",
                "border-color: var(--dr-active) !important;",
            ),
        ),
        (
            "secondary focus",
            r'button\[kind="secondary"\]:not\(:disabled\):focus-visible',
            ("outline-color: var(--dr-focus);",),
        ),
        (
            "secondary disabled",
            r'button\[kind="secondary"\]:disabled',
            (
                "background: var(--dr-disabled-bg) !important;",
                "border-color: var(--dr-disabled-border) !important;",
                "color: var(--dr-disabled-text) !important;",
            ),
        ),
        (
            "link normal",
            r'\[data-testid="stLinkButton"\]\s+'
            r'\[data-testid="stBaseLinkButton-secondary"\]',
            (
                "background: var(--dr-background) !important;",
                "border: 1px solid var(--dr-border) !important;",
                "color: var(--dr-text) !important;",
                "min-height: 44px;",
            ),
        ),
        (
            "link hover",
            r'\[data-testid="stLinkButton"\]\s+'
            r'\[data-testid="stBaseLinkButton-secondary"\]'
            r':not\(\[disabled\]\):hover',
            (
                "background: var(--dr-surface) !important;",
                "border-color: var(--dr-active) !important;",
                "color: var(--dr-text) !important;",
            ),
        ),
        (
            "link active",
            r'\[data-testid="stLinkButton"\]\s+'
            r'\[data-testid="stBaseLinkButton-secondary"\]'
            r':not\(\[disabled\]\):active',
            (
                "background: var(--dr-active-tint) !important;",
                "border-color: var(--dr-active) !important;",
                "color: var(--dr-text) !important;",
            ),
        ),
        (
            "link focus",
            r'\[data-testid="stLinkButton"\]\s+'
            r'\[data-testid="stBaseLinkButton-secondary"\]'
            r':not\(\[disabled\]\):focus-visible',
            (
                "outline: 3px solid var(--dr-focus);",
                "outline-offset: 2px;",
            ),
        ),
        (
            "link disabled",
            r'\[data-testid="stLinkButton"\]\s+'
            r'\[data-testid="stBaseLinkButton-secondary"\]\[disabled\],\s*'
            r'\[data-testid="stLinkButton"\]\s+'
            r'\[data-testid="stBaseLinkButton-secondary"\]\[disabled\]:hover,\s*'
            r'\[data-testid="stLinkButton"\]\s+'
            r'\[data-testid="stBaseLinkButton-secondary"\]\[disabled\]:active',
            (
                "background: var(--dr-disabled-bg) !important;",
                "border-color: var(--dr-disabled-border) !important;",
                "color: var(--dr-disabled-text) !important;",
                "cursor: not-allowed;",
            ),
        ),
    )

    for label, selector_pattern, declarations in contracts:
        body = _rule_body(css, selector_pattern)
        for declaration in declarations:
            assert declaration in body, f"{label} missing {declaration}"


def test_disabled_buttons_cannot_match_interactive_color_rules() -> None:
    css = styles.STATIC_CSS

    for selector_pattern in (
        r'button\[kind="primary"\]:not\(:disabled\):hover,\s*'
        r'button\[kind="primaryFormSubmit"\]:not\(:disabled\):hover',
        r'button\[kind="primary"\]:not\(:disabled\):active,\s*'
        r'button\[kind="primaryFormSubmit"\]:not\(:disabled\):active',
        r'button\[kind="secondary"\]:not\(:disabled\):hover',
        r'button\[kind="secondary"\]:not\(:disabled\):active',
    ):
        assert re.search(rf"{selector_pattern}\s*\{{", css)

    for selector_pattern in (
        r'\[data-testid="stLinkButton"\]\s+'
        r'\[data-testid="stBaseLinkButton-secondary"\]'
        r':not\(\[disabled\]\):hover',
        r'\[data-testid="stLinkButton"\]\s+'
        r'\[data-testid="stBaseLinkButton-secondary"\]'
        r':not\(\[disabled\]\):active',
    ):
        assert re.search(rf"{selector_pattern}\s*\{{", css)


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
    tablet_start = css.index("@media (max-width: 900px)")
    mobile_start = css.index("@media (max-width: 640px)", tablet_start)
    assert "padding-top: 64px;" in css[tablet_start:mobile_start]
    assert "padding-top: 64px;" in css[mobile_start:]


def test_main_content_clears_fixed_header_at_every_responsive_breakpoint() -> None:
    css = styles.STATIC_CSS
    tablet_start = css.index("@media (max-width: 900px)")
    mobile_start = css.index("@media (max-width: 640px)", tablet_start)
    breakpoint_sections = {
        "desktop": css[:tablet_start],
        "tablet": css[tablet_start:mobile_start],
        "mobile": css[mobile_start:],
    }

    for breakpoint, section in breakpoint_sections.items():
        body = _rule_body(
            section,
            r'\[data-testid="stMainBlockContainer"\]',
        )
        match = re.search(r"padding-top:\s*(?P<pixels>\d+)px;", body)
        assert match is not None, f"Missing top padding for {breakpoint}"
        assert int(match.group("pixels")) >= 60, (
            f"{breakpoint} top padding must clear Streamlit's 60px header"
        )


def test_shell_copy_markdown_boundary_removes_negative_bottom_spacing() -> None:
    body = _rule_body(
        styles.STATIC_CSS,
        r'\[data-testid="stMarkdownContainer"\]:has\(\.dr-shell-copy\)',
    )

    assert "margin-bottom: 0 !important;" in body


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
    assert 'button[kind="primary"]:not(:disabled):focus-visible' in css
    assert 'button[kind="primaryFormSubmit"]:not(:disabled):focus-visible' in css
    primary_focus_rule = re.search(
        r'button\[kind="primary"\]:not\(:disabled\):focus-visible,\s*'
        r'button\[kind="primaryFormSubmit"\]:not\(:disabled\):focus-visible\s*'
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

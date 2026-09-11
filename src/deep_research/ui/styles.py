"""Centralized visual tokens and the small static CSS boundary for the UI."""

from __future__ import annotations

COLORS = {
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

SPACING = {
    "1": "4px",
    "2": "8px",
    "3": "12px",
    "4": "16px",
    "6": "24px",
    "8": "32px",
    "10": "40px",
    "12": "48px",
}

RADII = {"control": "6px", "container": "8px"}
BORDERS = {"default": "1px solid #DCE2DF"}
SHADOWS = {"default": "none"}
WIDTHS = {
    "sidebar": "240-270px",
    "details_rail": "220-260px",
    "report": "720-820px",
    "canvas": "1120px",
}

REPORT_WIDTH = WIDTHS["report"]
SIDEBAR_WIDTH = WIDTHS["sidebar"]
DETAILS_RAIL_WIDTH = WIDTHS["details_rail"]
CANVAS_MAX_WIDTH = WIDTHS["canvas"]


STATIC_CSS = """
<style>
:root {
  --dr-background: #FCFCFA;
  --dr-surface: #F5F7F6;
  --dr-text: #172126;
  --dr-text-muted: #66727A;
  --dr-border: #DCE2DF;
  --dr-active: #0F6F68;
  --dr-active-tint: #E8F3F1;
  --dr-success: #2F7A4D;
  --dr-success-tint: #EAF4ED;
  --dr-warning: #946200;
  --dr-warning-tint: #FFF4D6;
  --dr-error: #B42318;
  --dr-error-tint: #FDECEA;
  --dr-space-1: 4px;
  --dr-space-2: 8px;
  --dr-space-3: 12px;
  --dr-space-4: 16px;
  --dr-space-6: 24px;
  --dr-space-8: 32px;
  --dr-space-10: 40px;
  --dr-space-12: 48px;
  --dr-radius-control: 6px;
  --dr-radius-container: 8px;
}

.stApp {
  background: var(--dr-background);
  color: var(--dr-text);
  color-scheme: light;
}

[data-testid="stAppViewContainer"] {
  background: var(--dr-background);
}

/* Native Streamlit chrome: keep Deploy/menu visible and readable. */
[data-testid="stAppViewContainer"] header,
[data-testid="stHeader"] {
  background: #FCFCFA;
  color: #172126;
  border-bottom: 1px solid #DCE2DF;
}

[data-testid="stHeader"] button,
[data-testid="stHeader"] svg {
  color: #172126;
  fill: #172126;
  opacity: 1;
}

[data-testid="stSidebar"] {
  min-width: 240px;
  max-width: 270px;
  border-right: 1px solid var(--dr-border);
  background: var(--dr-background);
}

[data-testid="stSidebarContent"] {
  display: flex;
  flex-direction: column;
}

[data-testid="stMainBlockContainer"] {
  max-width: 1120px;
  padding: 40px 48px 48px;
  padding-top: 64px;
}

.dr-editorial-column {
  max-width: 820px;
}

[class*="st-key-dr-new-research-column"],
[class*="st-key-dr-report-column"] {
  width: 100%;
  max-width: 820px;
  margin-right: auto;
}

[data-testid="stForm"] {
  width: 100%;
  max-width: 820px;
}

.dr-product-identity {
  display: flex;
  align-items: center;
  gap: var(--dr-space-3);
  padding: var(--dr-space-2) var(--dr-space-1) var(--dr-space-6);
  border-bottom: 1px solid var(--dr-border);
}

.dr-product-mark {
  display: inline-flex;
  width: 32px;
  height: 32px;
  align-items: center;
  justify-content: center;
  border-radius: var(--dr-radius-control);
  background: var(--dr-active);
  color: var(--dr-background);
  font-size: 16px;
}

.dr-product-name {
  color: var(--dr-text);
  font-size: 16px;
  font-weight: 600;
  line-height: 1.2;
}

.dr-product-subtitle,
.dr-meta,
.dr-shell-copy {
  color: var(--dr-text-muted);
  font-size: 13px;
  line-height: 1.55;
}

.dr-section-label {
  color: var(--dr-text-muted);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.06em;
  line-height: 1.4;
  text-transform: uppercase;
}

.dr-status {
  display: inline-flex;
  align-items: center;
  gap: var(--dr-space-1);
  font-size: 13px;
  font-weight: 600;
  line-height: 1.4;
}

.dr-status--neutral,
.dr-status--running {
  color: var(--dr-active);
}

.dr-status--completed {
  color: var(--dr-success);
}

.dr-status--max-iterations {
  color: var(--dr-warning);
}

.dr-status--incomplete {
  color: var(--dr-text-muted);
}

.dr-status--failed {
  color: var(--dr-error);
}

.dr-quality-list {
  display: flex;
  flex-direction: column;
  gap: var(--dr-space-1);
  margin: var(--dr-space-2) 0 var(--dr-space-4);
}

.dr-quality-row {
  display: grid;
  grid-template-columns: 16px 1fr auto;
  align-items: center;
  gap: var(--dr-space-2);
  color: var(--dr-text);
  font-size: 13px;
  line-height: 1.5;
}

.dr-quality-icon {
  display: inline-flex;
  width: 16px;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 700;
}

.dr-quality-row--success .dr-quality-icon {
  color: var(--dr-success);
}

.dr-quality-row--warning .dr-quality-icon {
  color: var(--dr-warning);
}

.dr-quality-row--error .dr-quality-icon {
  color: var(--dr-error);
}

.dr-quality-row--neutral .dr-quality-icon {
  color: var(--dr-text-muted);
}

.dr-quality-count {
  color: var(--dr-text-muted);
  font-variant-numeric: tabular-nums;
}

[data-testid="stCodeBlock"] pre,
[data-testid="stCode"] pre {
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.dr-session-question {
  color: var(--dr-text);
  font-size: 14px;
  line-height: 1.45;
  overflow-wrap: anywhere;
  padding: var(--dr-space-2) 0 var(--dr-space-1);
}

.st-key-dr-sidebar-spacer {
  flex: 1;
  min-height: var(--dr-space-8);
}

[class*="st-key-dr-session-row-"],
[class*="st-key-dr-history-row-"] {
  border-bottom: 1px solid var(--dr-border);
  padding: var(--dr-space-2) 0;
}

[class*="st-key-dr-session-row-selected-"],
[class*="st-key-dr-history-row-selected-"] {
  border-radius: var(--dr-radius-container);
  background: var(--dr-active-tint);
  padding: var(--dr-space-2);
}

.dr-shell-rule {
  border-top: 1px solid var(--dr-border);
  margin: var(--dr-space-8) 0;
}

.dr-shell-title,
.dr-shell-title h1,
.dr-shell-title h2 {
  color: var(--dr-text);
  font-family: Georgia, "Times New Roman", serif;
  font-weight: 600;
  letter-spacing: -0.02em;
}

.dr-shell-title h1 {
  font-size: 42px;
  line-height: 1.15;
}

.dr-shell-title h2 {
  font-size: 28px;
  line-height: 1.25;
}

.dr-editorial-column p,
.dr-editorial-column li {
  color: var(--dr-text);
  font-size: 16px;
  line-height: 1.65;
}

.stButton > button {
  border-radius: var(--dr-radius-control);
  box-shadow: none;
  min-height: 44px;
}

button[kind="primary"],
button[kind="primaryFormSubmit"] {
  background: var(--dr-active) !important;
  border-color: var(--dr-active) !important;
  color: var(--dr-background) !important;
  min-height: 44px !important;
}

button[kind="secondary"] {
  background: var(--dr-background) !important;
  border-color: var(--dr-border) !important;
  color: var(--dr-text) !important;
}

[data-testid="stTextArea"] textarea,
[data-testid="stNumberInputField"] {
  background: var(--dr-background) !important;
  color: var(--dr-text) !important;
}

[data-testid="stTextAreaRootElement"],
[data-testid="stNumberInputContainer"] {
  background: var(--dr-background) !important;
  border: 1px solid var(--dr-border) !important;
  border-radius: var(--dr-radius-control) !important;
}

[data-testid="stTextArea"] textarea {
  min-height: 152px;
}

[data-testid="stNumberInputContainer"] input {
  background: transparent !important;
}

[data-testid="stNumberInputContainer"] [data-testid^="stNumberInputStep"] {
  background: var(--dr-surface) !important;
  color: var(--dr-text) !important;
}

.dr-readonly-field {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 44px;
  background: #F5F7F6;
  border: 1px solid var(--dr-border);
  border-radius: var(--dr-radius-control);
  color: var(--dr-text);
  padding: var(--dr-space-2) var(--dr-space-3);
}

.dr-control-label {
  margin: 0 0 8px;
  color: #172126;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.35;
}

.dr-readonly-meta {
  color: var(--dr-text-muted);
  font-size: 12px;
}

.dr-screen-eyebrow {
  margin: 0 0 8px;
}

.dr-subsection-heading {
  margin: 0;
}

.dr-next-steps {
  margin-top: 32px;
  padding-top: 16px;
  border-top: 1px solid #DCE2DF;
}

.dr-next-steps > div {
  padding-top: 8px;
}

button:focus-visible,
a:focus-visible,
input:focus-visible,
textarea:focus-visible,
[role="radio"]:focus-visible {
  outline: 3px solid #0F6F68;
  outline-offset: 2px;
}

button[kind="primary"]:focus-visible,
button[kind="primaryFormSubmit"]:focus-visible {
  outline-color: #172126;
}

input:disabled,
textarea:disabled,
button:disabled {
  opacity: 1;
  cursor: not-allowed;
}

[data-testid="stSidebar"] .stButton > button {
  white-space: normal;
  overflow-wrap: anywhere;
  text-align: left;
}

@media (max-width: 900px) {
  [data-testid="stMainBlockContainer"] {
    padding: 24px;
    padding-top: 56px;
  }

  /* Keep the form's weighted columns readable beside the persistent sidebar. */
  [data-testid="stForm"] [data-testid="stHorizontalBlock"] {
    flex-wrap: wrap;
  }

  [data-testid="stForm"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
    min-width: 100% !important;
  }

  .dr-shell-title h1 {
    font-size: 36px;
  }
}

@media (max-width: 640px) {
  [data-testid="stMainBlockContainer"] {
    padding-top: 48px;
  }
}
</style>
"""

# These aliases keep the public style entry point easy to discover for callers
# that prefer a more descriptive name than STATIC_CSS.
CSS = STATIC_CSS
APP_CSS = STATIC_CSS


__all__ = [
    "APP_CSS",
    "BORDERS",
    "CANVAS_MAX_WIDTH",
    "COLORS",
    "CSS",
    "DETAILS_RAIL_WIDTH",
    "RADII",
    "REPORT_WIDTH",
    "SHADOWS",
    "SIDEBAR_WIDTH",
    "SPACING",
    "STATIC_CSS",
    "WIDTHS",
]

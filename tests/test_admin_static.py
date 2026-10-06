"""Tests that the vendored assets are intact and the stylesheet stays offline and accessible."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

STATIC = Path("src/yokai_admin/static")


def _css() -> str:
    return (STATIC / "panel.css").read_text(encoding="utf-8")


def test_vendored_files_match_their_recorded_hashes() -> None:
    notes = (STATIC / "VENDORED.md").read_text(encoding="utf-8")
    entries = re.findall(r"^- (\S+) sha256:([0-9a-f]{64})$", notes, flags=re.MULTILINE)
    assert {name for name, _ in entries} >= {"vendor/htmx.min.js", "fonts/Archivo-variable.woff2"}
    for name, digest in entries:
        assert hashlib.sha256((STATIC / name).read_bytes()).hexdigest() == digest, name


def test_the_font_license_ships_with_the_font() -> None:
    assert "SIL Open Font License" in (STATIC / "fonts" / "OFL.txt").read_text(encoding="utf-8")


def test_htmx_is_the_minified_2x_build() -> None:
    text = (STATIC / "vendor" / "htmx.min.js").read_text(encoding="utf-8")
    assert "htmx" in text and len(text) > 40_000 and 'version:"2.' in text


def test_the_stylesheet_references_nothing_external() -> None:
    css = _css()
    assert "http://" not in css and "https://" not in css and "@import" not in css


def test_the_stylesheet_defines_the_palette_and_both_themes() -> None:
    css = _css()
    for token in ("--hull", "--panel", "--ink", "--slate", "--line", "--sonar"):
        assert f"{token}:" in css
    for token in ("--flare", "--lichen", "--amber"):
        assert f"{token}:" in css
    assert "prefers-color-scheme: dark" in css


def test_motion_respects_reduced_motion_and_focus_is_visible() -> None:
    css = _css()
    assert "prefers-reduced-motion: reduce" in css
    assert ":focus-visible" in css


def test_status_markers_differ_by_shape_not_only_color() -> None:
    css = _css()
    assert '.mark[data-status="warn"]' in css and '.mark[data-status="error"]' in css
    assert "border-radius: 0" in css


def test_the_panel_script_marks_the_status_block_stale_when_polling_fails() -> None:
    script = (STATIC / "panel.js").read_text(encoding="utf-8")
    for event in ("htmx:sendError", "htmx:responseError", "htmx:timeout"):
        assert event in script
    assert "data-stale" in script and "eval(" not in script
    assert '[data-stale="true"]' in _css()


def test_form_and_table_styles_exist_and_use_palette_tokens_only() -> None:
    css = _css()
    for selector in (
        ".btn",
        ".btn.danger",
        ".table",
        ".flash",
        ".add",
        ".link-danger",
        ".page-title",
    ):
        assert selector in css
    button_rule = css.split(".btn {")[1].split("}")[0]
    assert "#" not in button_rule  # buttons use palette tokens, never literal colors


def test_the_phone_layout_cannot_be_stretched_by_a_wide_table() -> None:
    """Found in a real browser: with a plain `1fr` track a wide table widened the whole page and
    clipped text on the right. The phone grid must use minmax(0, 1fr) and tables must scroll
    inside their own wrapper."""
    css = _css()
    phone = css.split("@media (max-width: 48rem) {")[1]
    assert "grid-template-columns: minmax(0, 1fr);" in phone.split("}")[0]
    wrapper = css.split(".table-wrap {")[1].split("}")[0]
    assert "overflow-x: auto;" in wrapper

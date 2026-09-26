"""Tests for the embed HTML preview tool."""

from __future__ import annotations

import html
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from tests.embed_helpers import emoji_count, total_embed_length


def _load() -> ModuleType:
    path = Path(__file__).resolve().parent.parent / "scripts" / "preview_embeds.py"
    spec = importlib.util.spec_from_file_location("preview_embeds", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preview = _load()


def test_md_to_html_escapes_html_and_renders_basic_markdown() -> None:
    html_out = preview.md_to_html("<script>alert(1)</script> **bold** *it* `code` <@42>")
    assert "<script>" not in html_out
    assert "&lt;script&gt;" in html_out
    assert "<strong>bold</strong>" in html_out
    assert "<em>it</em>" in html_out
    assert "<code>code</code>" in html_out
    assert "@user" in html_out and "&lt;@42&gt;" not in html_out


def test_md_to_html_links_and_escaped_brackets() -> None:
    linked = preview.md_to_html("**[Song](https://www.youtube.com/watch?v=abc)**")
    assert '<a href="https://www.youtube.com/watch?v=abc"' in linked
    escaped = preview.md_to_html("\\[not a link\\](http://evil.example)")
    assert "<a " not in escaped
    assert "[not a link]" in escaped


def test_every_sample_embed_obeys_the_layout_rules() -> None:
    samples = preview.build_samples()
    assert len(samples) >= 14
    labels = [label for label, _ in samples]
    assert len(labels) == len(set(labels))
    for label, embed in samples:
        assert emoji_count(embed) <= 1, label
        assert total_embed_length(embed) <= 6000, label
        assert len(embed.fields) <= 25, label


def test_render_page_is_a_fragment_containing_every_embed() -> None:
    samples = preview.build_samples()
    page = preview.render_page(samples)
    assert page.lstrip().startswith('<meta charset="utf-8">')
    assert "<html" not in page and "<body" not in page and "<head" not in page
    for label, embed in samples:
        assert label in page
        if embed.title:
            assert html.escape(embed.title.split(" ", 1)[-1][:20]) in page


def test_render_embed_shows_fields_in_a_grid_and_color_bar() -> None:
    samples = dict(preview.build_samples())
    embed = next(e for label, e in samples.items() if "Now playing" in label)
    rendered = preview.render_embed(embed, "Now playing")
    assert "border-left-color:#00e5ff" in rendered.lower().replace(" ", "")
    assert "Requested by" in rendered and "Duration" in rendered
    assert 'class="field' in rendered


def test_main_writes_the_preview_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "preview" / "embeds.html"
    monkeypatch.setattr("sys.argv", ["preview_embeds.py", "--out", str(out)])
    assert preview.main() == 0
    assert out.exists() and out.stat().st_size > 2000

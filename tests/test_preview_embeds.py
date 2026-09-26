"""Tests for the embed HTML preview tool."""

from __future__ import annotations

import html
import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import discord
import pytest

from tests.embed_helpers import emoji_count, total_embed_length
from yokai.ui.embeds import EmbedFactory


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
    assert len(samples) >= 24
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
    error = next(e for label, e in samples.items() if label.startswith("Error (with hint"))
    assert "border-left-color:#ff1744" in preview.render_embed(error, "x").lower().replace(" ", "")


def test_main_writes_the_preview_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "preview" / "embeds.html"
    monkeypatch.setattr("sys.argv", ["preview_embeds.py", "--out", str(out)])
    assert preview.main() == 0
    assert out.exists() and out.stat().st_size > 2000


def test_link_urls_cannot_break_out_of_the_href_attribute() -> None:
    hostile = (
        '[x](https://a.com/"onmouseover=alert(1))',
        "[x](https://a.com/'onmouseover=alert(1))",
        '**[x](https://a.com/"onmouseover=alert(1))**',
    )
    for text in hostile:
        out = preview.md_to_html(text)
        assert '"onmouseover' not in out and "'onmouseover" not in out, text
        match = re.search(r'<a href="([^"]*)"', out)
        assert match, text
        assert "'" not in match.group(1), text
        assert "&quot;onmouseover" in out or "&#x27;onmouseover" in out, text


def test_non_http_links_are_inert() -> None:
    assert "<a " not in preview.md_to_html("[x](javascript:alert(1))")
    assert "<a " not in preview.md_to_html("**[x](data:text/html,hi)**")
    embed = discord.Embed(title="Hostile", url="javascript:alert(1)", description="d")
    rendered = preview.render_embed(embed, "x")
    assert "javascript:" not in rendered and "<a " not in rendered
    good = preview.render_embed(discord.Embed(title="Ok", url="https://a.example/x"), "x")
    assert '<a href="https://a.example/x">Ok</a>' in good


def test_md_to_html_escaped_text_stays_plain() -> None:
    out = preview.md_to_html(
        r"**[Song \(Remastered\) \[live\]](https://www.youtube.com/watch?v=a)**"
    )
    assert (
        '<a href="https://www.youtube.com/watch?v=a"><strong>Song (Remastered) [live]</strong></a>'
        in out
    )
    inert = preview.md_to_html(r"\[free gift\]\(http://evil\)")
    assert "<a " not in inert and "[free gift](http://evil)" in inert
    literal = preview.md_to_html(r"\*Bold\* and \`c\` and \<@42\>")
    assert "*Bold* and `c` and <@42>".replace("<", "&lt;").replace(">", "&gt;") in literal
    assert "<em>" not in literal and "<code>" not in literal and "mention" not in literal


def test_render_embed_escapes_hostile_text_everywhere() -> None:
    payload = "<script>alert(1)</script><img src=x onerror=alert(1)></style>"
    embed = discord.Embed(title=payload, description=payload)
    embed.add_field(name=payload, value=payload, inline=True)
    embed.set_footer(text=payload, icon_url="https://img.example/a.png")
    rendered = preview.render_embed(embed, payload)
    for needle in ("<script", "<img", "</style"):
        assert needle not in rendered, needle
    assert "&lt;script&gt;" in rendered


def test_field_names_are_not_rendered_as_markdown() -> None:
    embed = discord.Embed(title="t")
    embed.add_field(name="*star* `tick`", value="*v*", inline=True)
    rendered = preview.render_embed(embed, "x")
    assert "*star* `tick`" in rendered
    assert "<em>v</em>" in rendered


def test_escaped_markdown_from_the_real_builders_renders_literally() -> None:
    title = "*Bold* and `c` [x](http://evil.example)"
    embeds = [
        EmbedFactory.queued(title=title, position=1, artist="A*rt"),
        EmbedFactory.queue_page(
            tracks=[(1, title, "A*rt", 100)], page=1, total_pages=1, total_tracks=1
        ),
        EmbedFactory.recommendations(tracks=[(title, "A*rt", 100)], seed_title=title),
    ]
    for embed in embeds:
        rendered = preview.render_embed(embed, "x")
        assert "*Bold*" in rendered and "`c`" in rendered
        assert "[x](http://evil.example)" in rendered
        assert "<em>Bold" not in rendered and "<code>c</code>" not in rendered
        assert '<a href="http://evil' not in rendered
        for leftover in (r"\<", r"\*", r"\`", r"\["):
            assert leftover not in rendered, leftover


def test_footer_icon_only_when_the_embed_has_an_icon() -> None:
    with_icon = EmbedFactory.info("t", "d", bot_avatar_url="https://img.example/a.png")
    without = EmbedFactory.info("t", "d")
    assert 'class="icon"' in preview.render_embed(with_icon, "x")
    assert 'class="icon"' not in preview.render_embed(without, "x")


def test_ping_samples_cover_all_three_status_colors() -> None:
    samples = dict(preview.build_samples())
    colors = {label: samples[label].color.value for label in samples if label.startswith("Ping (")}
    assert len(set(colors.values())) == 3, colors


def test_generating_twice_gives_identical_output(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parent.parent / "scripts" / "preview_embeds.py"
    outputs = []
    for name in ("a.html", "b.html"):
        target = tmp_path / name
        subprocess.run([sys.executable, str(script), "--out", str(target)], check=True)
        outputs.append(target.read_bytes())
    assert outputs[0] == outputs[1]
    assert b"\r\n" not in outputs[0]

"""The guild wiki viewer: a section menu plus Prev, Contents and Next buttons."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from yokai.rpg.wiki import SECTIONS, Wiki
from yokai.ui.embeds import EmbedFactory
from yokai.ui.table_common import avatar_url
from yokai.ui.views import BaseView

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

WIKI_TIMEOUT = 300.0
CONTENTS = "contents"


class WikiSectionSelect(discord.ui.Select):  # type: ignore[type-arg]
    """Jump to the first page of a section (or back to the contents)."""

    def __init__(self, view: WikiView) -> None:
        options = [discord.SelectOption(label="Contents", value=CONTENTS, description="Start here")]
        options += [
            discord.SelectOption(label=title, value=section_id, description=summary[:100])
            for section_id, title, summary in SECTIONS
        ]
        super().__init__(placeholder="Jump to a section", options=options)
        self.wiki_view = view

    async def callback(self, interaction: discord.Interaction) -> None:
        self.wiki_view.index = self.wiki_view.wiki.first_page[self.values[0]]
        await self.wiki_view.show(interaction)


class WikiView(BaseView):
    """Private to the member who opened it. Times out and disables itself."""

    def __init__(
        self, bot: YokaiBot, wiki: Wiki, user_id: int, index: int = 0, timeout: float = WIKI_TIMEOUT
    ) -> None:
        super().__init__(allowed_user_ids=[user_id], timeout=timeout)
        self.bot = bot
        self.wiki = wiki
        self.index = min(max(index, 0), len(wiki.pages) - 1)
        self.select = WikiSectionSelect(self)
        self.add_item(self.select)
        self._sync()

    def _sync(self) -> None:
        last = len(self.wiki.pages) - 1
        self.prev_button.disabled = self.index <= 0
        self.contents_button.disabled = self.index == 0
        self.next_button.disabled = self.index >= last
        current = self.wiki.pages[self.index].section_id
        for option in self.select.options:
            option.default = option.value == current

    def render(self) -> discord.Embed:
        page = self.wiki.pages[self.index]
        title = page.title if page.section_id == "contents" else f"Wiki: {page.title}"
        return EmbedFactory.wiki_page(
            title,
            page.body,
            self.index + 1,
            len(self.wiki.pages),
            bot_avatar_url=avatar_url(self.bot),
        )

    async def show(self, interaction: discord.Interaction) -> None:
        self._sync()
        await interaction.response.edit_message(embed=self.render(), view=self)

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary, row=1)
    async def prev_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.index = max(0, self.index - 1)
        await self.show(interaction)

    @discord.ui.button(label="Contents", style=discord.ButtonStyle.primary, row=1)
    async def contents_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.index = 0
        await self.show(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=1)
    async def next_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.index = min(len(self.wiki.pages) - 1, self.index + 1)
        await self.show(interaction)

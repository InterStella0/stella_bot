from __future__ import annotations

import datetime
import operator
from typing import AsyncGenerator, Any, List, Dict, Union

import discord
from discord.ext import commands
from fuzzywuzzy import fuzz

from cogs.find_bot.baseclass import FindBotCog
from cogs.find_bot.models import BotRepo
from utils.buttons import InteractionPages
from utils.decorators import wait_ready, event_check, pages
from utils.errors import ErrorNoSignature
from utils.useful import StellaContext, StellaEmbed, plural, aware_utc, aislice


class GithubHandler(FindBotCog):
    @commands.Cog.listener("on_message")
    @wait_ready()
    @event_check(lambda _, m: m.author.bot)
    async def is_it_bot_repo(self, message: discord.Message):
        def get_content(m: discord.Message) -> str:
            content_inner = m.content
            if m.embeds:
                embed = m.embeds[0]
                content_inner += " / " + str(embed.to_dict())
            return content_inner

        content = get_content(message)
        bot = message.author
        potential = []
        for match in self.re_github.finditer(content):
            repo_name = match['repo_name']
            predicting_name = fuzz.ratio(repo_name, bot.name)
            predicting_display = fuzz.ratio(repo_name, bot.display_name)
            if (predict := max([predicting_display, predicting_name])) >= 50:
                potential.append((match, predict))

        if potential:
            match, predict = max(potential, key=operator.itemgetter(1))
            sql = "INSERT INTO bot_repo VALUES($1, $2, $3, $4) " \
                  "ON CONFLICT (bot_id) DO UPDATE SET owner_repo=$2, bot_name=$3, certainty=$4 " \
                  "WHERE bot_repo.certainty < $4"
            values = (bot.id, match["repo_owner"], match["repo_name"], predict)
            await self.bot.pool_pg.execute(sql, *values)

    @commands.command(aliases=["wgithub", "github", "botgithub"], help="Tries to show the given bot's GitHub repository.")
    async def whatgithub(self, ctx: StellaContext, bot: BotRepo):
        async def formatted_commits() -> AsyncGenerator[str, None]:
            async for c in aislice(repo.get_commits(), 5):
                commit = c['commit']
                time_created = datetime.datetime.strptime(commit['author']['date'], "%Y-%m-%dT%H:%M:%SZ")
                message = commit['message']
                url = c['html_url']
                sha = c['sha'][:6]
                yield f'[{aware_utc(time_created, mode="R")}] [{message}]({url} "{sha}")'

        repo = bot.repo
        try:
            author = await self.bot.git.get_user(repo.owner.login)
            value = [f'{u.login}(`{u.contributions}`)' async for u in aislice(repo.get_contributors(), 3)]
        except Exception as e:
            raise ErrorNoSignature(str(e))

        embed = StellaEmbed.default(
            ctx,
            title=repo.full_name,
            description=f"**About: **\n{repo.description}\n\n**Recent Commits:** \n" +
                        "\n".join([o async for o in formatted_commits()]) +
                        plural("\n\n**Top Contributor(s)**\n", len(value)) + ", ".join(value),
            url=repo.html_url
        )
        embed.set_thumbnail(url=bot.bot.display_avatar)
        embed.add_field(name=plural("Star(s)", repo.stargazers_count), value=repo.stargazers_count)
        embed.add_field(name=plural("Fork(s)", repo.forks_count), value=repo.forks_count)
        embed.add_field(name="Language", value=repo.language)

        if issue := repo.open_issues_count:
            embed.add_field(name=plural("Open Issue(s)", issue), value=issue)

        embed.add_field(name="Created At", value=aware_utc(repo.created_at))
        embed.set_author(name=f"Repository by {author.name}", icon_url=author.avatar_url)
        await ctx.maybe_reply(embed=embed)

    @commands.command(aliases=["agithub", "ag", "everygithub", "allgithubs"],
                      help="Shows all bot's github that it knows from a server.")
    async def allgithub(self, ctx: StellaContext):
        bots = [b.id for b in ctx.guild.members if b.bot]
        data = await self.bot.pool_pg.fetch("SELECT * FROM bot_repo WHERE bot_id=ANY($1::BIGINT[])", bots)

        if not data:
            return await ctx.reply("I dont know any github here.")

        @pages(per_page=6)
        async def each_git_list(instance, menu_inter: InteractionPages,
                                entries: List[Dict[str, Union[str, int]]]) -> discord.Embed:
            offset = menu_inter.current_page * instance.per_page
            embed = StellaEmbed(title=f"All GitHub Repository in {ctx.guild}")
            members = [ctx.guild.get_member(b['bot_id']) for b in entries]
            contents = ["{i}. [{m}](https://github.com/{owner_repo}/{bot_name})".format(i=i, m=m, **b)
                        for (i, b), m in zip(enumerate(entries, start=offset + 1), members)]
            embed.description = "\n".join(contents)
            return embed

        menu = InteractionPages(each_git_list(data))
        await menu.start(ctx)

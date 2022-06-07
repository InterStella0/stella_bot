from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import discord

from utils import cog
from utils.cog import StellaCog
from utils.ipc import IPCData
from utils.useful import count_source_lines

if TYPE_CHECKING:
    from main import StellaBot


class ServerHandler(StellaCog):
    def __init__(self, bot: StellaBot):
        self.bot = bot

    @cog.server_request()
    async def on_get_info(self, data: IPCData) -> None:
        return {
            "guild_amount": len(self.bot.guilds),
            "user_amount": len(self.bot.users),
            "latency": self.bot.latency,
            "launch_time": self.bot.uptime.isoformat(),
            "codelines": count_source_lines('.'),
            "last_commands": [
                {
                    "author": str(ctx.author),
                    "command": ctx.command.qualified_name,
                    "created_at": ctx.message.created_at.isoformat()
                }
                for ctx in [*self.bot.cached_context][:-10:-1]
            ]
        }

    @cog.server_request()
    async def on_get_invite(self, data: IPCData) -> None:
        return {"invite": discord.utils.oauth_url(self.bot.user.id)}

    @cog.server_listen()
    async def on_restarting_server(self, _: IPCData) -> None:
        print("Server restarting...")
        server = self.bot.ipc_client
        await server.session.close()
        print("Server waiting for server respond.")
        await asyncio.sleep(10)
        print("Server re-establishing connection")
        await server.init_sock()
        print("Server Connection Successful.")

    @cog.server_listen()
    async def on_kill(self, data: IPCData) -> None:
        print("Kill has been ordered", data)
        await self.bot.close()


async def setup(bot: StellaBot) -> None:
    await bot.add_cog(ServerHandler(bot))

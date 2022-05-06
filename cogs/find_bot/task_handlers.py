from __future__ import annotations
import datetime
import itertools

import discord

from cogs.find_bot.baseclass import FindBotCog
from utils.useful import print_exception


class TaskHandler(FindBotCog):
    TASK_ID = 1001
    EVERY_SEQUENCE = datetime.timedelta(days=45)

    async def task_handler(self):
        for count in itertools.count(1):
            try:
                print("Executing Sequence no", count)
                await self.execute_task_at()
            except Exception as e:
                print_exception("Error while executing task: ", e)

    async def execute_task_at(self):
        data = await self.bot.pool_pg.fetchrow("SELECT * FROM bot_tasks WHERE task_id=$1", self.TASK_ID)
        current = datetime.datetime.now(datetime.timezone.utc)
        if not data:
            query = "INSERT INTO bot_tasks VALUES ($1, $2, $3)"
            next_time = current + self.EVERY_SEQUENCE
            await self.bot.pool_pg.execute(query, self.TASK_ID, current, next_time)
            await self.on_purge_old_pending()
        else:
            exec_time = data["next_execution"]
            if exec_time <= current:
                next_time = current + self.EVERY_SEQUENCE
                query = "UPDATE bot_tasks SET last_execution=$1, next_execution=$2 WHERE task_id=$3"
                await self.bot.pool_pg.execute(query, current, next_time, self.TASK_ID)
                await self.on_purge_old_pending()
            else:
                next_time = exec_time

        await discord.utils.sleep_until(next_time)

    async def on_purge_old_pending(self):
        far_time = datetime.datetime.utcnow() - self.EVERY_SEQUENCE
        await self.bot.pool_pg.execute("DELETE FROM pending_bots WHERE requested_at <= $1", far_time)

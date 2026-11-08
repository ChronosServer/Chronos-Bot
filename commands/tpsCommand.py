import asyncio
import discord
from discord.ext import commands
import json
import os
import sys
sys.path.append('./Chronos-Library/')
from tpsLibrary import tps_check

f = open('config.json')
data = json.load(f)
server_name = data['bot']['server_name']
server_cfg = data['server']

SERVER_WORLD_PATHS = {
    k.replace('_path', ''): os.path.join(server_cfg[k], server_cfg[k.replace('_path', '_world_name')])
    for k in server_cfg
    if k.endswith('_path') and k.replace('_path', '_world_name') in server_cfg
}
f.close()


class tps(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(
        name='tps',
        description="Show each server's average TPS (ticks per second) over the last 300 seconds",
        help='Shows average TPS from the last 300 seconds, Usage: `!!tps`'
    )
    async def tps(self, ctx):
        await ctx.defer()
        # every server's level.dat is read in its own thread
        results = await asyncio.gather(*(asyncio.to_thread(tps_check, path) for path in SERVER_WORLD_PATHS.values()))

        embed = discord.Embed(title=f'{server_name} TPS')
        for server, result in zip(SERVER_WORLD_PATHS, results):
            embed.add_field(name=server.upper(), value=result, inline=False)
        embed.set_footer(text='Chronos™')
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(tps(bot))
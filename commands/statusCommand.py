import asyncio
import discord
from discord.ext import commands
import json
import sys
sys.path.append('./Chronos-Library/')
from statusLibrary import status_check, storage_check

f = open('config.json')
data = json.load(f)
rcon_pass = data['server']['rcon_pass']
server_name = data['bot']['server_name']
storage_status_block_coords = data['server']['storage_status_block_coords']
server_cfg = data['server']

RCON_PORTS = {
    k.replace('_rcon_port', ''): v
    for k, v in server_cfg.items()
    if k.endswith('_rcon_port')
}
smp_rcon_port = RCON_PORTS['smp']
f.close()


class status(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(
        name='status',
        description='Show whether each Minecraft server is online, and if Main Storage is running',
        help=f'Show {server_name} status, Usage: `!!status`'
    )
    async def status(self, ctx):
        await ctx.defer()
        # every check runs at the same time, in threads, so one dead server can't stall the rest
        results = await asyncio.gather(
            *(asyncio.to_thread(status_check, port, rcon_pass) for port in RCON_PORTS.values()),
            asyncio.to_thread(storage_check, storage_status_block_coords, smp_rcon_port, rcon_pass)
        )
        embed = discord.Embed(title=f'Status of {server_name} servers')
        for server, result in zip(RCON_PORTS, results):
            embed.add_field(name=server.upper(), value=result, inline=False)
        embed.add_field(name='Main Storage', value=results[-1], inline=False)
        embed.set_footer(text='Chronos™')
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(status(bot))
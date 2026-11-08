import asyncio
import discord
from discord.ext import commands
import json
import os
import sys
sys.path.append('./Chronos-Library/')
from filesizeLibrary import get_size_format, get_file_size

f = open('config.json')
data = json.load(f)
server_name = data['bot']['server_name']
server_cfg = data['server']

SERVER_WORLD_PATHS = {
    k.replace('_path', ''): os.path.join(server_cfg[k], server_cfg[k.replace('_path', '_world_name')])
    for k in server_cfg
    if k.endswith('_path') and k.replace('_path', '_world_name') in server_cfg
}
recordings_path = os.path.join(server_cfg['smp_path'], 'recordings')
webserver_path = server_cfg['webserver_path']
f.close()

def safe_get_size(path):
    try:
        return get_size_format(get_file_size(path))
    except (FileNotFoundError, PermissionError):
        return 'N/A'

class worldsize(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(
        name='worldsize',
        description='Show the size on disk of every server world, the SMP recordings and the webserver',
        help=f'Display {server_name} world size, Usage: `!!worldsize`'
    )
    async def worldsize(self, ctx):
        await ctx.defer()
        # walking the world folders is slow, so every folder is measured in its own thread
        paths = list(SERVER_WORLD_PATHS.values()) + [recordings_path, webserver_path]
        sizes = await asyncio.gather(*(asyncio.to_thread(safe_get_size, path) for path in paths))

        embed = discord.Embed(title=f'{server_name} World Size')
        for server, size in zip(SERVER_WORLD_PATHS, sizes):
            embed.add_field(name=server.upper(), value=size, inline=False)
        embed.add_field(name='SMP Recordings', value=sizes[-2], inline=False)
        embed.add_field(name='Webserver', value=sizes[-1], inline=False)
        embed.set_footer(text='Chronos™')
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(worldsize(bot))
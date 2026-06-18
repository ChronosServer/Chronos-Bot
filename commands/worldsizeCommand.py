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

    @commands.command(help=f'Display {server_name} world size, Usage: `!!worldsize`')
    async def worldsize(self, ctx):
        embed = discord.Embed(title=f'{server_name} World Size')
        for server, world_path in SERVER_WORLD_PATHS.items():
            embed.add_field(name=server.upper(), value=safe_get_size(world_path), inline=False)
        embed.add_field(name='SMP Recordings', value=safe_get_size(recordings_path), inline=False)
        embed.add_field(name='Webserver', value=safe_get_size(webserver_path), inline=False)
        embed.set_footer(text='Chronos™')
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(worldsize(bot))
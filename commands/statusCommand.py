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

    @commands.command(help=f'Show {server_name} status, Usage: `!!status`')
    async def status(self, ctx):
        embed = discord.Embed(title=f'Status of {server_name} servers')
        for server, port in RCON_PORTS.items():
            embed.add_field(name=server.upper(), value=status_check(port, rcon_pass), inline=False)
        embed.add_field(name='Main Storage', value=storage_check(storage_status_block_coords, smp_rcon_port, rcon_pass), inline=False)
        embed.set_footer(text='Chronos™')
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(status(bot))
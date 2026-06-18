# commands/onlineCommand.py
import discord
from discord.ext import commands
import json
import sys
sys.path.append('./Chronos-Library/')
from restartLibrary import rcon, handle_list

f = open('config.json')
data = json.load(f)
rcon_pass = data['server']['rcon_pass']
server_cfg = data['server']
RCON_PORTS = {
    k.replace('_rcon_port', ''): v
    for k, v in server_cfg.items()
    if k.endswith('_rcon_port')
}
f.close()


class online(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(help='Show online players across all servers, Usage: `!!online`')
    async def online(self, ctx):
        embed = discord.Embed(title='Chronos online players')
        total = 0
        lines = []

        for server, port in RCON_PORTS.items():
            response = rcon(port, rcon_pass, 'list')
            if response is None:
                lines.append(f'[{server.upper()}] Offline')
                continue

            players = handle_list(response) or []
            count = len(players)
            total += count

            if count == 0:
                lines.append(f'[{server.upper()}] (0)')
            else:
                lines.append(f'[{server.upper()}] ({count}): {", ".join(players)}')

        embed.description = '\n'.join(lines)
        embed.set_footer(text=f'Total players online: {total}  •  Chronos™')
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(online(bot))
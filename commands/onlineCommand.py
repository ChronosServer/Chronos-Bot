# commands/onlineCommand.py
import asyncio
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

    @commands.hybrid_command(
        name='online',
        description='Show which players are online on each Minecraft server',
        help='Show online players across all servers, Usage: `!!online`'
    )
    async def online(self, ctx):
        await ctx.defer()
        embed = discord.Embed(title='Chronos online players')
        total = 0
        lines = []

        # query every server at the same time, in threads, so one dead server can't stall the rest
        responses = await asyncio.gather(*(asyncio.to_thread(rcon, port, rcon_pass, 'list') for port in RCON_PORTS.values()))

        for server, response in zip(RCON_PORTS, responses):
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
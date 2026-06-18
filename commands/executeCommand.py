import discord
from discord.ext import commands
from rcon import Client
import json
import socket

f = open('config.json')
data = json.load(f)
rcon_pass = data['server']['rcon_pass']
RCON_PORTS = {
    k.replace('_rcon_port', ''): v
    for k, v in data['server'].items()
    if k.endswith('_rcon_port')
}
f.close()

class execute(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(help='Execute commands with rcon on servers, Usage: `!!execute <server> <command>` (Admin Only)')
    @commands.has_permissions(administrator=True)
    async def execute(self, ctx, server, *, arg):
        rcon_port = RCON_PORTS.get(server)
        if rcon_port is None:
            await ctx.send(embed=discord.Embed(description=f"Unknown server `{server}`. Valid options: {', '.join(RCON_PORTS)}").set_footer(text='Chronos™'))
            return
        try:
            with Client('127.0.0.1', int(rcon_port), passwd=rcon_pass, timeout=1.5) as client:
                response = client.run(arg)
            if response:
                await ctx.send(embed=discord.Embed(description=f"`{response}`").set_footer(text='Chronos™'))
        except socket.timeout:
            await ctx.send(embed=discord.Embed(description="Couldn't reach the server in time").set_footer(text='Chronos™'))
        except ConnectionRefusedError:
            await ctx.send(embed=discord.Embed(description=f"Couldn't reach `{server}` — server is offline or RCON is unavailable").set_footer(text='Chronos™'))

async def setup(bot):
    await bot.add_cog(execute(bot))
import discord
from discord import app_commands
from discord.ext import commands
from rcon import Client
import json
import socket
from typing import Literal

f = open('config.json')
data = json.load(f)
rcon_pass = data['server']['rcon_pass']
RCON_PORTS = {
    k.replace('_rcon_port', ''): v
    for k, v in data['server'].items()
    if k.endswith('_rcon_port')
}
f.close()

SERVERS = Literal[tuple(RCON_PORTS)]

class execute(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(
        name='execute',
        description='Run a console command on a Minecraft server via RCON (admin only)',
        help='Execute commands with rcon on servers, Usage: `!!execute <server> <command>` (Admin Only)'
    )
    @app_commands.describe(
        server='Server to run the command on',
        command='Command to run, without the leading / (e.g. list, time set day, whitelist reload)'
    )
    @app_commands.default_permissions(administrator=True)
    @commands.has_permissions(administrator=True)
    async def execute(self, ctx, server: SERVERS, *, command: str):
        await ctx.defer()
        rcon_port = RCON_PORTS[server]
        try:
            with Client('127.0.0.1', int(rcon_port), passwd=rcon_pass, timeout=1.5) as client:
                response = client.run(command)
            if response:
                await ctx.send(embed=discord.Embed(description=f"`{response}`").set_footer(text='Chronos™'))
            else:
                await ctx.send(embed=discord.Embed(description=f"Command ran on `{server}` (no output).").set_footer(text='Chronos™'))
        except socket.timeout:
            await ctx.send(embed=discord.Embed(description="Couldn't reach the server in time").set_footer(text='Chronos™'))
        except ConnectionRefusedError:
            await ctx.send(embed=discord.Embed(description=f"Couldn't reach `{server}` — server is offline or RCON is unavailable").set_footer(text='Chronos™'))

async def setup(bot):
    await bot.add_cog(execute(bot))
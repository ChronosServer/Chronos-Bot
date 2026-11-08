import discord
from discord import app_commands
from discord.ext import commands
from rcon import Client
import json
import socket
from typing import Literal

# reads config
f = open('config.json')
data = json.load(f)
smp_rcon_port = data['server']['smp_rcon_port']
rcon_pass = data['server']['rcon_pass']
colors = data['server']['colors']
f.close()

COLORS = Literal[tuple(colors)]

# color command
class color(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(
        name='color',
        description="Change a player's color on the SMP, or list the available colors (admin only)",
        help='Change player color on SMP, Usage: `!!color <change/list> <color> <player>` (Admin Only)'
    )
    @app_commands.describe(
        option='change = give a player a color, list = show the available colors',
        color='Color to give the player (change only)',
        player='Minecraft username of the player (change only)'
    )
    @app_commands.default_permissions(administrator=True)
    @commands.has_permissions(administrator=True)
    async def color(self, ctx, option: Literal['change', 'list'], color: COLORS = None, player: str = None):
        await ctx.defer()
        if option == 'list':
            embed = discord.Embed(
                title = 'List of Colors'
            )
            for item in colors:
                embed.add_field(name = '\u200b', value = item, inline=False)
            embed.set_footer(text='Chronos™')
            await ctx.send(embed=embed)
        elif option == 'change':
            if color is None or player is None:
                await ctx.send(embed=discord.Embed(description='Provide both a color and a player.').set_footer(text='Chronos™'))
                return
            try:
                with Client('127.0.0.1', int(smp_rcon_port), passwd=rcon_pass, timeout=1.5) as client:
                    response = client.run('team join ' + color + ' ' + player)
                embed = discord.Embed(
                    description = response or f'Changed the color of {player} to {color}.'
                )
                embed.set_footer(text='Chronos™')
                await ctx.send(embed=embed)
            except socket.timeout:
                embed = discord.Embed(
                    description = "Couldn't reach the server in time"
                )
                embed.set_footer(text='Chronos™')
                await ctx.send(embed=embed)
            except ConnectionRefusedError:
                embed = discord.Embed(
                    description = "Couldn't reach the SMP — server is offline or RCON is unavailable"
                )
                embed.set_footer(text='Chronos™')
                await ctx.send(embed=embed)

async def setup(bot): # a extension must have a setup function
	await bot.add_cog(color(bot)) # adding a cog
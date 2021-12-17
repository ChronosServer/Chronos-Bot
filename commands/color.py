import discord
from discord.ext import commands
from rcon import Client
import json
import socket

# reads config
f = open('config.json')
data = json.load(f)
smp_rcon_port = data['smp_rcon_port']
rcon_pass = data['rcon_pass']
colors = data['colors']
f.close()

# color command
class color(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    @commands.command(help = 'Change player color on SMP, Usage: `!!color <change/list> <color> <player>` (Admin Only)')
    @commands.has_permissions(administrator=True)
    async def color(self, ctx, option, arg_color = None, player = None, ):
        if option == 'list':
            embed = discord.Embed(
                title = 'List of Colors'
            )
            for item in colors:
                embed.add_field(name = '\u200b', value = item, inline=False)
            embed.set_footer(text='Chronos™'),
            await ctx.send(embed=embed)
        if option == 'change':
            if player != '':
                try:
                    with Client('127.0.0.1', int(smp_rcon_port), passwd=rcon_pass, timeout=1.5) as client:
                        response = client.run('team join ' + arg_color + ' ' + player)
                    if response == '':
                        print
                    elif response != '':
                        embed = discord.Embed(
                            description = response
                        )
                        embed.set_footer(text='Chronos™'),
                        await ctx.send(embed=embed)
                except socket.timeout:
                    embed = discord.Embed(
                        description = "Couldn't reach the server in time"
                    )
                    embed.set_footer(text='Chronos™'),
                    await ctx.send(embed=embed)

def setup(bot): # a extension must have a setup function
	bot.add_cog(color(bot)) # adding a cog
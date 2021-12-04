import discord
from discord.ext import commands
from rcon import Client
import json

# reads config
f = open('config.json')
data = json.load(f)
smp_rcon_port = data['smp_rcon_port']
cmp_rcon_port = data['cmp_rcon_port']
cmp2_rcon_port = data['cmp2_rcon_port']
cmp3_rcon_port = data['cmp3_rcon_port']
mirror_rcon_port = data['mirror_rcon_port']
rcon_pass = data['rcon_pass']
f.close()

# execute command
class execute(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    @commands.command(help = 'Execute commands with rcon on servers, Usage: `!!execute <smp/cmp/cmp2/cmp3/mirror> <command>` (Admin Only)')
    @commands.has_permissions(administrator=True)
    async def execute(self, ctx, server, *, arg,):
        if arg != '':
                if server == 'smp':
                    with Client('127.0.0.1', int(smp_rcon_port), passwd=rcon_pass) as client:
                        response = client.run(arg)
                        if response == '':
                            print
                        elif response != '':
                            embed = discord.Embed(
                            description = response
                            )
                            embed.set_footer(text='Chronos™'),
                            await ctx.send(embed=embed)
                elif server == 'cmp':
                    with Client('127.0.0.1', int(cmp_rcon_port), passwd=rcon_pass) as client:
                        response = client.run(arg)
                        if response == '':
                            print
                        elif response != '':
                            embed = discord.Embed(
                                description = response
                            )
                            embed.set_footer(text='Chronos™'),
                            await ctx.send(embed=embed)
                elif server == 'cmp2':
                    with Client('127.0.0.1', int(cmp2_rcon_port), passwd=rcon_pass) as client:
                        response = client.run(arg)
                        if response == '':
                            print
                        elif response != '':
                            embed = discord.Embed(
                                description = response
                            )
                            embed.set_footer(text='Chronos™'),
                            await ctx.send(embed=embed)
                elif server == 'cmp3':
                    with Client('127.0.0.1', int(cmp3_rcon_port), passwd=rcon_pass) as client:
                        response = client.run(arg)
                        if response == '':
                            print
                        elif response != '':
                            embed = discord.Embed(
                                description = response
                            )
                            embed.set_footer(text='Chronos™'),
                            await ctx.send(embed=embed)
                elif server == 'mirror':
                    with Client('127.0.0.1', int(mirror_rcon_port), passwd=rcon_pass) as client:
                        response = client.run(arg)
                        if response == '':
                            print
                        elif response != '':
                            embed = discord.Embed(
                                description = response
                            )
                            embed.set_footer(text='Chronos™'),
                            await ctx.send(embed=embed)
                else:
                    return


def setup(bot): # a extension must have a setup function
	bot.add_cog(execute(bot)) # adding a cog
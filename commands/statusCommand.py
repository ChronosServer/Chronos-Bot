import discord
from discord.ext import commands
from rcon import Client
import json
import sys
sys.path.append('./Chronos-Library/')
from statusLibrary import status_check
from statusLibrary import storage_check

# reads config
f = open('config.json')
data = json.load(f)
smp_rcon_port = data['server']['smp_rcon_port']
cmp_rcon_port = data['server']['cmp_rcon_port']
cmp2_rcon_port = data['server']['cmp2_rcon_port']
cmp3_rcon_port = data['server']['cmp3_rcon_port']
mirror_rcon_port = data['server']['mirror_rcon_port']
rcon_pass = data['server']['rcon_pass']
server_name = data['bot']['server_name']
storage_status_block_coords = data['server']['storage_status_block_coords']
f.close()

# ping command
class status(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    @commands.command(help = 'Show ' + server_name + ' TPS, Usage: `!!status`')
    async def status(self, ctx):
        embed = discord.Embed(
            title = 'Status of ' + server_name + ' servers',
        )
        embed.add_field(name = 'SMP', value = status_check(smp_rcon_port, rcon_pass), inline = False),
        embed.add_field(name = 'CMP', value = status_check(cmp_rcon_port, rcon_pass), inline = False),
        embed.add_field(name = 'CMP2', value = status_check(cmp2_rcon_port, rcon_pass), inline = False),
        embed.add_field(name = 'CMP3', value = status_check(cmp3_rcon_port, rcon_pass), inline = False),
        embed.add_field(name = 'MIRROR', value = status_check(mirror_rcon_port, rcon_pass), inline = False),
        embed.add_field(name = 'Main Storage', value = storage_check(storage_status_block_coords, smp_rcon_port, rcon_pass), inline = False)
        embed.set_footer(text='Chronos™'),
        await ctx.send(embed=embed)


def setup(bot): # a extension must have a setup function
	bot.add_cog(status(bot)) # adding a cog
import discord
from discord.ext import commands
import json
from nbt import nbt
import sys
sys.path.append('./Chronos-Library/')
from tpsLibrary import tps_check

# reads config
f = open('config.json')
data = json.load(f)
smp_path = data['smp_path']
cmp_path = data['cmp_path']
cmp2_path = data['cmp2_path']
cmp3_path = data['cmp3_path']
mirror_path = data['mirror_path']
server_name = data['server_name']
f.close()

# ping command
class tps(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    @commands.command(help = 'Shows avarage TPS from the last 300 seconds, Usage: `!!tps`')
    async def tps(self, ctx):
        embed = discord.Embed(
            title = server_name + ' TPS',
        )
        embed.add_field(name = 'SMP', value = tps_check(smp_path), inline = False),
        embed.add_field(name = 'CMP', value = tps_check(cmp_path), inline = False),
        embed.add_field(name = 'CMP2', value = tps_check(cmp2_path), inline = False),
        embed.add_field(name = 'CMP3', value = tps_check(cmp3_path), inline = False),
        embed.add_field(name = 'MIRROR', value = tps_check(mirror_path), inline = False),
        embed.set_footer(text='Chronos™'),
        await ctx.send(embed=embed)

def setup(bot): # a extension must have a setup function
	bot.add_cog(tps(bot)) # adding a cog
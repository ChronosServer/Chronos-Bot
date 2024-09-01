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
smp_path = data['server']['smp_path']
cmp_path = data['server']['cmp_path']
cmp2_path = data['server']['cmp2_path']
cmp3_path = data['server']['cmp3_path']
cmp4_path = data['server']['cmp4_path']
mirror_path = data['server']['mirror_path']
snapshot_path = data['server']['snapshot_path']
building_path = data['server']['building_path']
server_name = data['bot']['server_name']
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
        embed.add_field(name = 'SMP', value = tps_check(smp_path + 'world-smp0'), inline = False),
        embed.add_field(name = 'CMP', value = tps_check(cmp_path + 'world-cmp0'), inline = False),
        embed.add_field(name = 'CMP2', value = tps_check(cmp2_path + 'world-cmp0'), inline = False),
        embed.add_field(name = 'CMP3', value = tps_check(cmp3_path + 'world-cmp0'), inline = False),
        embed.add_field(name = 'CMP4', value = tps_check(cmp4_path + 'world-cmp4'), inline = False),
        embed.add_field(name = 'MIRROR', value = tps_check(mirror_path + 'world-mirror0'), inline = False),
        embed.add_field(name = 'SNAPSHOT', value = tps_check(snapshot_path + 'world-snapshot0'), inline = False),
        embed.add_field(name = 'BUILDING', value = tps_check(building_path + 'world-building0'), inline = False),
        embed.set_footer(text='Chronos™'),
        await ctx.send(embed=embed)

async def setup(bot): # a extension must have a setup function
	await bot.add_cog(tps(bot)) # adding a cog
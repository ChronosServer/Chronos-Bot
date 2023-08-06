import discord
from discord import client
from discord.ext import commands
import json
import sys
sys.path.append('./Chronos-Library/')
from filesizeLibrary import get_size_format
from filesizeLibrary import get_file_size

# reads config
f = open('config.json')
data = json.load(f)
server_name = data['bot']['server_name']
smp_path = data['server']['smp_path']
cmp_path = data['server']['cmp_path']
cmp2_path = data['server']['cmp2_path']
cmp3_path = data['server']['cmp3_path']
mirror_path = data['server']['mirror_path']
pcrc_recordings_path = data['server']['pcrc_recordings_path']
webserver_path = data['server']['webserver_path']
f.close()

# worldsize command
class worldsize(commands.Cog):
    def __init__(self, client):
        self.client = client
    @commands.command(help = 'Display ' + server_name + ' world size, Usage `!!worldsize`')
    async def worldsize(self, ctx):
        embed = discord.Embed(
            title = server_name + ' World Size',
        )
        embed.add_field(name='SMP', value=get_size_format(get_file_size(smp_path + '/world-smp0')), inline=False)
        embed.add_field(name='CMP', value=get_size_format(get_file_size(cmp_path + '/world-cmp0')), inline=False)
        embed.add_field(name='CMP2', value=get_size_format(get_file_size(cmp2_path + '/world-cmp0')), inline=False)
        embed.add_field(name='CMP3', value=get_size_format(get_file_size(cmp3_path + '/world-cmp0')), inline=False)
        embed.add_field(name='MIRROR', value=get_size_format(get_file_size(mirror_path + '/world-mirror0')), inline=False)
        embed.add_field(name='PCRC Recordings', value=get_size_format(get_file_size(pcrc_recordings_path)), inline=False)
        embed.add_field(name='Webserver', value=get_size_format(get_file_size(webserver_path)), inline=False)
        embed.set_footer(text='Chronos™'),
        await ctx.send(embed=embed)

async def setup(bot): # a extension must have a setup function
	await bot.add_cog(worldsize(client)) # adding a cog
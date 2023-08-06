import discord
from discord.ext import commands
from discord import client
import psutil
import json

# reads config
f = open('config.json')
data = json.load(f)
cpu_hardware = data['server']['cpu_hardware']
ram_hardware = data['server']['ram_hardware']
f.close()

# hardware command
class hardware(commands.Cog):
    def __init__(self, client):
        self.client = client
    @commands.command(help = 'Display cpu and ram usage, Usage: `!!hardware`')
    async def hardware(self, ctx):
        embed = discord.Embed(
            title = 'Hardware usage'
        )
        embed.add_field(name='CPU ' + cpu_hardware, value="Current CPU usage is: **" + str(psutil.cpu_percent(interval=0.5)) + "%**" , inline=False)
        embed.add_field(name=ram_hardware + ' RAM', value="Current RAM usage is: **" + str(round(psutil.virtual_memory()[3] / 1073741824, 2)) + "%**" , inline=False)
        embed.set_footer(text='Chronos™')
        await ctx.send(embed=embed)
    
async def setup(bot): # a extension must have a setup function
	await bot.add_cog(hardware(client)) # adding a cog
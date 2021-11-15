import discord
from discord import client
from discord.ext import commands
import psutil

class hardware(commands.Cog):
    def __init__(self, client):
        self.client = client
    @commands.command()
    async def hardware(self, ctx):
        embed = discord.Embed(
            title = 'Hardware usage',
        )
        embed.add_field(name='CPU', value="Current CPU usage is: **" + str(psutil.cpu_percent(interval=0.5)) + "%**" , inline=False)
        embed.add_field(name='RAM', value="Current RAM usage is: **" + str(round(psutil.virtual_memory()[3] / 1073741824, 2)) + "%**" , inline=False)
        embed.set_footer(text='Chronos™'),
        await ctx.send(embed=embed)
    

def setup(bot): # a extension must have a setup function
	bot.add_cog(hardware(client)) # adding a cog
import discord
from discord.ext import commands

# ping command 
class ping(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    @commands.command(help = 'Query bot ping')
    async def ping(self, ctx):
        embed = discord.Embed(
            description = 'Pong! ' + str(round(self.bot.latency * 1000)) + ' ms'
        )
        embed.set_footer(text='Chronos™'),
        await ctx.send(embed=embed)    

def setup(bot): # a extension must have a setup function
	bot.add_cog(ping(bot)) # adding a cog
import discord
from discord.ext import commands

# example command 
class example(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    @commands.command(help = 'description here, Usage: `!!example`')
    async def ping(self, ctx):
        embed = discord.Embed(
            description = 'example command 101'
        )
        embed.set_footer(text='Chronos™'),
        await ctx.send(embed=embed)    

def setup(bot): # a extension must have a setup function
	bot.add_cog(example(bot)) # adding a cog
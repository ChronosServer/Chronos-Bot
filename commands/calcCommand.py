import discord
from discord.ext import commands
from simpleeval import simple_eval

# calc command
class calc(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    @commands.command(help = 'Simple calculator, Usage: `!!calc`')
    async def calc(self, ctx, *, calc_data):
        embed = discord.Embed(
            description = simple_eval(calc_data)
        )
        embed.set_footer(text='Chronos™'),
        await ctx.send(embed=embed)

def setup(bot): # a extension must have a setup function
	bot.add_cog(calc(bot)) # adding a cog
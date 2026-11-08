import discord
from discord import app_commands
from discord.ext import commands
from simpleeval import simple_eval

# calc command
class calc(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(
        name='calc',
        description='Evaluate a math expression, e.g. 64 * 27 / 3',
        help='Simple calculator, Usage: `!!calc <expression>`'
    )
    @app_commands.describe(expression='Math expression to evaluate, e.g. 64 * 27 / 3 or (2 + 3) ** 2')
    async def calc(self, ctx, *, expression: str):
        try:
            result = simple_eval(expression)
        except Exception as e:
            await ctx.send(embed=discord.Embed(description=f"Couldn't evaluate `{expression}`: {e}").set_footer(text='Chronos™'))
            return
        embed = discord.Embed(description=str(result))
        embed.set_footer(text='Chronos™')
        await ctx.send(embed=embed)

async def setup(bot): # a extension must have a setup function
	await bot.add_cog(calc(bot)) # adding a cog
import discord
from discord import client
from discord.ext import commands

class setstatus(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def setstatus(self, ctx, statusname):
        await self.bot.change_presence(status=discord.Status.online,
                                 activity=discord.Game(name=str(statusname), type=discord.ActivityType.listening))
        embed = discord.Embed(
            title = 'Changed bot status to ' + statusname
            )
        embed.set_footer(text='Chronos™'),
        await ctx.send(embed=embed)
    
def setup(bot): # a extension must have a setup function
	bot.add_cog(setstatus(bot)) # adding a cog
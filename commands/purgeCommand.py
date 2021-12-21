import discord
from discord import client
from discord.ext import commands

client = discord.Client() # defines 'client'

# purge command
class purge(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    @commands.command(help = 'Purge messages, Usage:`!!purge <amount>` (Admin Only)')
    @commands.has_permissions(administrator=True)
    async def purge(self, ctx, limit: int):
        if limit > 100:
            embed = discord.Embed(
                description = "Couldn't purge messages, " + str(limit) + " was over the limit"
            )
            embed.set_footer(text='Chronos™'),
            await ctx.send(embed=embed)
        elif limit < 100:
            await ctx.channel.purge(limit=limit)
            embed = discord.Embed(
                description = 'Cleared ' + str(limit) + ' message(s)'
            )
            embed.set_footer(text='Chronos™'),
            await ctx.send(embed=embed)

def setup(bot): # a extension must have a setup function
	bot.add_cog(purge(client)) # adding a cog
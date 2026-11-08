import discord
from discord import app_commands
from discord.ext import commands
import sys
sys.path.append('./Chronos-Library/')
from confirmLibrary import confirm

# purge command
class purge(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(
        name='purge',
        description='Delete the most recent messages in this channel (admin only)',
        help='Purge messages, Usage:`!!purge <amount>` (Admin Only)'
    )
    @app_commands.describe(limit='How many of the most recent messages to delete (1-100)')
    @app_commands.default_permissions(administrator=True)
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def purge(self, ctx, limit: commands.Range[int, 1, 100]):
        # ephemeral so the "thinking" placeholder and the confirm prompt aren't among the messages that get purged
        await ctx.defer(ephemeral=True)
        if not await confirm(ctx, f"Delete the last **{limit}** message(s) in {ctx.channel.mention}? This can't be undone."):
            return
        # with a !! command the command message and the prompt are in the channel too, so only purge what came before them
        before = ctx.message if ctx.interaction is None else None
        await ctx.channel.purge(limit=limit, before=before)
        embed = discord.Embed(
            description = 'Cleared ' + str(limit) + ' message(s)'
        )
        embed.set_footer(text='Chronos™')
        await ctx.send(embed=embed, ephemeral=True)

async def setup(bot): # a extension must have a setup function
	await bot.add_cog(purge(bot)) # adding a cog
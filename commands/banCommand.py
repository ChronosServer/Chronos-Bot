import discord
from discord import app_commands
from discord.ext import commands
import sys
sys.path.append('./Chronos-Library/')
from confirmLibrary import confirm

# ban command
class ban(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(
        name='ban',
        description='Ban a user by ID, optionally deleting their recent messages',
        help='Ban users with user id, Usage: `!!ban <id> <delete message days> <reason>` (Admin Only)'
    )
    @app_commands.describe(
        user_id='ID of the user to ban (works for users who are not in the server)',
        delete_message_days='Days of their messages to delete, 0-7 (default 0)',
        reason='Reason for the ban (shown in the audit log)'
    )
    @app_commands.default_permissions(ban_members=True)
    @commands.has_permissions(ban_members=True)
    @commands.guild_only()
    async def ban(self, ctx, user_id: str, delete_message_days: commands.Range[int, 0, 7] = 0, *, reason: str = None):
        await ctx.defer()
        try:
            user = await self.bot.fetch_user(int(user_id))
        except (ValueError, discord.NotFound):
            await ctx.send(embed=discord.Embed(description=f'No user found with ID `{user_id}`.').set_footer(text='Chronos™'))
            return
        if user == ctx.author:
            await ctx.send(embed=discord.Embed(description="You can't ban yourself.").set_footer(text='Chronos™'))
            return
        details = f'Ban **{user}** (`{user.id}`) and delete {delete_message_days} day(s) of their messages?'
        if reason:
            details += f'\nReason: {reason}'
        if not await confirm(ctx, details):
            return
        try:
            await ctx.guild.ban(user, reason=reason, delete_message_seconds=delete_message_days * 86400)
        except discord.HTTPException as e:
            await ctx.send(embed=discord.Embed(description=f"Couldn't ban `{user_id}`: {e.text}").set_footer(text='Chronos™'))
            return
        embed = discord.Embed(title='Banned user', description=f'<@{user.id}>')
        embed.set_footer(text='Chronos™')
        await ctx.send(embed=embed)

async def setup(bot): # a extension must have a setup function
	await bot.add_cog(ban(bot)) # adding a cog
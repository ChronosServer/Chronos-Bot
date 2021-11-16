import discord
from discord.ext import commands

# ban command
class ban(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    @commands.command(help = 'Ban users with user id (Admin Only)')
    @commands.has_permissions(ban_members = True)
    async def ban(self, ctx, user_id, reason = None, delete_message_days = 0):
        user = await self.bot.fetch_user(user_id)
        if user == ctx.author:
            pass
        else:
            embed = discord.Embed(
                title = 'Banned user', description = '<@!' + user_id + '>'
                )
            embed.set_footer(text='Chronos™'),
            await ctx.send(embed=embed)   
            await ctx.guild.ban(user, reason = reason, delete_message_days = delete_message_days) 

def setup(bot): # a extension must have a setup function
	bot.add_cog(ban(bot)) # adding a cog
import discord
from discord import app_commands
from discord.ext import commands
import json

f = open('config.json')
data = json.load(f)
default_status = data['bot']['default_status']
f.close()

class setstatus(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(
        name='setstatus',
        description="Change the bot's status text (admin only)",
        help='Set bot status, Usage: `!!setstatus <status>` (Admin Only)'
    )
    @app_commands.describe(statusname='Status text to show (leave empty to reset to the default)')
    @app_commands.default_permissions(administrator=True)
    @commands.has_permissions(administrator=True)
    async def setstatus(self, ctx, *, statusname: str = None):
        statusname = statusname or default_status
        await self.bot.change_presence(status=discord.Status.online,
                                       activity=discord.Game(name=statusname, type=discord.ActivityType.listening))
        embed = discord.Embed(title=f'Changed bot status to {statusname}')
        embed.set_footer(text='Chronos™')
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(setstatus(bot))
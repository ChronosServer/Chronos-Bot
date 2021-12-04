from discord.ext import commands

# ---config---
full_member_id = 864956435313197059
# memberping command 
class memberping(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def memberping(self, ctx):
        await ctx.send('<@&' + full_member_id + '>')    

def setup(bot): # a extension must have a setup function
	bot.add_cog(memberping(bot)) # adding a cog
import discord
from discord.ext import commands
import json

# reads config
f = open('config.json')
data = json.load(f)
member_role = data['bot']['member_role']
f.close()

# region command 
class region(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    @commands.has_role(int(member_role))
    @commands.command(help = 'Get region files from SMP, Usage: `!!region <smp/cmp/cmp2/cmp3/mirror> <ow/nether/end> <region (Example: r.0.0)>`')
    async def region(self, ctx, server, dimension, region):
        f = open('config.json')
        data = json.load(f)
        server_path = data[server + '_path']
        f.close()
        if dimension == 'end':
            region_folder_path = '/DIM1/region/'
        if dimension == 'ow':
            region_folder_path = '/region/'
        if dimension == 'nether':
            region_folder_path = '/DIM-1/region/'
        embed = discord.Embed(
            title = server.upper() + ' ' + dimension.upper() + ' region ' + region
        )
        embed.set_footer(text='Chronos™'),
        await ctx.send(embed=embed)
        await ctx.send(file=discord.File(str(server_path) + region_folder_path + region + '.mca'))

def setup(bot): # a extension must have a setup function
    bot.add_cog(region(bot)) # adding a cog
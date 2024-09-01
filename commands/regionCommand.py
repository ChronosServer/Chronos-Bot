import discord
from discord.ext import commands
import json

# reads config
f = open('config.json')
data = json.load(f)
member_role = data['bot']['member_role']
smp_path = data['server']['smp_path']
cmp_path = data['server']['cmp_path']
cmp2_path = data['server']['cmp2_path']
cmp3_path = data['server']['cmp3_path']
cmp4_path = data['server']['cmp4_path']
mirror_path = data['server']['mirror_path']
snapshot_path = data['server']['snapshot_path']
building_path = data['server']['building_path']
f.close()

# region command 
class region(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    @commands.has_role(int(member_role))
    @commands.command(help = 'Get region files from servers, Usage: `!!region <smp/cmp/cmp2/cmp3/cmp4/mirror/snapshot/building> <ow/nether/end> <region (Example: r.0.0)>`')
    async def region(self, ctx, server, dimension, region):
        if server == 'smp':
            server_path = smp_path + 'world-smp0'
        if server == 'cmp':
            server_path = cmp_path + 'world-cmp0'
        if server == 'cmp2':
            server_path = cmp2_path + 'world-cmp0'
        if server == 'cmp3':
            server_path = cmp3_path + 'world-cmp0'
        if server == 'cmp4':
            server_path = cmp3_path + 'world-cmp4'
        if server == 'mirror':
            server_path = mirror_path + 'world-mirror0'
        if server == 'snapshot':
            server_path = snapshot_path + 'world-snapshot0'
        if server == 'building':
            server_path = building_path + 'world-building0'
        if dimension == 'end':
            region_folder_path = '/DIM1/region/'
        if dimension == 'ow':
            region_folder_path = '/region/'
        if dimension == 'nether':
            region_folder_path = '/DIM-1/region/'
        if dimension == 'end':
            region_entities_folder_path = '/DIM1/entities/'
        if dimension == 'ow':
            region_entities_folder_path = '/entities/'
        if dimension == 'nether':
            region_entities_folder_path = '/DIM-1/entities/'
        embed = discord.Embed(
            title = server.upper() + ' ' + dimension.upper() + ' region ' + region + ' and 2nd file will be the entities file '
        )
        embed.set_footer(text='Chronos™'),
        await ctx.send(embed=embed)
        await ctx.send(file=discord.File(str(server_path) + region_folder_path + region + '.mca'))
        await ctx.send(file=discord.File(str(server_path) + region_entities_folder_path + region + '.mca'))

async def setup(bot): # a extension must have a setup function
    await bot.add_cog(region(bot)) # adding a cog
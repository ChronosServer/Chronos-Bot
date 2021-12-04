import discord
from discord.ext import commands
import json

# reads config
f = open('config.json')
data = json.load(f)
smp_path = data['smp_path']
cmp_path = data['cmp_path']
cmp2_path = data['cmp2_path']
cmp3_path = data['cmp3_path']
mirror_path = data['mirror_path']
f.close()

# region command 
class region(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    @commands.has_role(890473812917891092)
    @commands.command(help = 'Get region files from SMP, Usage: `!!region <smp/cmp/cmp2/cmp3/mirror> <ow/nether/end> <region (Example: r.0.0)>`')
    async def region(self, ctx, server, dimension, region):
        if server == 'smp':
            if dimension == 'ow':
                embed = discord.Embed(
                title = server.upper() + ' overworld region ' + region
                )
                embed.set_footer(text='Chronos™'),
                await ctx.send(embed=embed)
                await ctx.send(file=discord.File(smp_path + '/region/' + region + '.mca'))
            elif dimension == 'nether':
                embed = discord.Embed(
                title = server.upper() + ' nether region ' + region
                )
                embed.set_footer(text='Chronos™'),
                await ctx.send(embed=embed)
                await ctx.send(file=discord.File(smp_path + '/DIM-1/region/' + region + '.mca'))
            elif dimension == 'end':
                embed = discord.Embed(
                title = server.upper() + ' end region ' + region
                )
                embed.set_footer(text='Chronos™'),
                await ctx.send(embed=embed)
                await ctx.send(file=discord.File(smp_path + '/DIM1/region/' + region + '.mca'))
            else:
                embed = discord.Embed(
                title = 'Invalid arguments'
                )
                embed.set_footer(text='Chronos™'),
                await ctx.send(embed=embed)
        if server == 'cmp':
            if dimension == 'ow':
                embed = discord.Embed(
                title = server.upper() + ' overworld region ' + region
                )
                embed.set_footer(text='Chronos™'),
                await ctx.send(embed=embed)
                await ctx.send(file=discord.File(cmp_path + '/region/' + region + '.mca'))
            elif dimension == 'nether':
                embed = discord.Embed(
                title = server.upper() + ' nether region ' + region
                )
                embed.set_footer(text='Chronos™'),
                await ctx.send(embed=embed)
                await ctx.send(file=discord.File(cmp_path + '/DIM-1/region/' + region + '.mca'))
            elif dimension == 'end':
                embed = discord.Embed(
                title = server.upper() + ' end region ' + region
                )
                embed.set_footer(text='Chronos™'),
                await ctx.send(embed=embed)
                await ctx.send(file=discord.File(cmp_path + '/DIM1/region/' + region + '.mca'))
            else:
                embed = discord.Embed(
                title = 'Invalid arguments'
                )
                embed.set_footer(text='Chronos™'),
                await ctx.send(embed=embed)
        if server == 'cmp2':
            if dimension == 'ow':
                embed = discord.Embed(
                title = server.upper() + ' overworld region ' + region
                )
                embed.set_footer(text='Chronos™'),
                await ctx.send(embed=embed)
                await ctx.send(file=discord.File(cmp2_path + '/region/' + region + '.mca'))
            elif dimension == 'nether':
                embed = discord.Embed(
                title = server.upper() + ' nether region ' + region
                )
                embed.set_footer(text='Chronos™'),
                await ctx.send(embed=embed)
                await ctx.send(file=discord.File(cmp2_path + '/DIM-1/region/' + region + '.mca'))
            elif dimension == 'end':
                embed = discord.Embed(
                title = server.upper() + ' end region ' + region
                )
                embed.set_footer(text='Chronos™'),
                await ctx.send(embed=embed)
                await ctx.send(file=discord.File(cmp2_path + '/DIM1/region/' + region + '.mca'))
            else:
                embed = discord.Embed(
                title = 'Invalid arguments'
                )
                embed.set_footer(text='Chronos™'),
                await ctx.send(embed=embed)
        if server == 'cmp3':
            if dimension == 'ow':
                embed = discord.Embed(
                title = server.upper() + ' overworld region ' + region
                )
                embed.set_footer(text='Chronos™'),
                await ctx.send(embed=embed)
                await ctx.send(file=discord.File(cmp3_path + '/region/' + region + '.mca'))
            elif dimension == 'nether':
                embed = discord.Embed(
                title = server.upper() + ' nether region ' + region
                )
                embed.set_footer(text='Chronos™'),
                await ctx.send(embed=embed)
                await ctx.send(file=discord.File(cmp3_path + '/DIM-1/region/' + region + '.mca'))
            elif dimension == 'end':
                embed = discord.Embed(
                title = server.upper() + ' end region ' + region
                )
                embed.set_footer(text='Chronos™'),
                await ctx.send(embed=embed)
                await ctx.send(file=discord.File(cmp3_path + '/DIM1/region/' + region + '.mca'))
            else:
                embed = discord.Embed(
                title = 'Invalid arguments'
                )
                embed.set_footer(text='Chronos™'),
                await ctx.send(embed=embed)
        if server == 'mirror':
            if dimension == 'ow':
                embed = discord.Embed(
                title = server.upper() + ' overworld region ' + region
                )
                embed.set_footer(text='Chronos™'),
                await ctx.send(embed=embed)
                await ctx.send(file=discord.File(mirror_path + '/region/' + region + '.mca'))
            elif dimension == 'nether':
                embed = discord.Embed(
                title = server.upper() + ' nether region ' + region
                )
                embed.set_footer(text='Chronos™'),
                await ctx.send(embed=embed)
                await ctx.send(file=discord.File(mirror_path + '/DIM-1/region/' + region + '.mca'))
            elif dimension == 'end':
                embed = discord.Embed(
                title = server.upper() + ' end region ' + region
                )
                embed.set_footer(text='Chronos™'),
                await ctx.send(embed=embed)
                await ctx.send(file=discord.File(mirror_path + '/DIM1/region/' + region + '.mca'))
            else:
                embed = discord.Embed(
                title = 'Invalid arguments'
                )
                embed.set_footer(text='Chronos™'),
                await ctx.send(embed=embed)

def setup(bot): # a extension must have a setup function
    bot.add_cog(region(bot)) # adding a cog
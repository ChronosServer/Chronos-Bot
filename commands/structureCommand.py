import discord
from discord.ext import commands
import json
import os
import sys
sys.path.append('./Chronos-Library/')
from filesizeLibrary import get_size_format
from filesizeLibrary import get_file_size

# reads config
f = open('config.json')
data = json.load(f)
cmp_structure_path = data['server']['cmp_structure_path']
member_role = data['bot']['member_role']
f.close()

# structure command 
class structure(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    @commands.command(help = 'Download and upload structure files to/from cmp, Usage: `!!structure <download/upload/list> <structure name (Only used for download, without .nbt)>` (Member Only)')
    @commands.has_role(int(member_role))
    async def structure(self, ctx, strucaction, strucname=None):
        if strucaction == 'upload':
            if str(ctx.message.attachments) == "[]": # Checks if there is an attachment on the message
                return
            else: # If there is it gets the filename from message.attachments
                split_v1 = str(ctx.message.attachments).split("filename='")[1]
                filename = str(split_v1).split("' ")[0]
                if filename.endswith(".nbt"): # Checks if it is a .nbt file
                    await ctx.message.attachments[0].save(fp=cmp_structure_path + filename.format(filename)) # saves the file
                    embed = discord.Embed(
                    title = filename + ' has successfully been uploaded to CMP'
                    )
                    embed.add_field(name='Structure filesize is', value=get_size_format(ctx.message.attachments[0].size), inline=False)
                    embed.set_footer(text='Chronos™'),
                    await ctx.send(embed=embed)
        elif strucaction == 'download':
            embed = discord.Embed(
            title = 'Structure ' + strucname, description = 'Filesize is ' + get_size_format(get_file_size(cmp_structure_path + strucname + '.nbt'))
            )
            embed.set_footer(text='Chronos:tm:'),
            await ctx.send(embed=embed)
            await ctx.send(file=discord.File(cmp_structure_path + strucname + '.nbt'))
        elif strucaction == 'list':
            strucfiles = os.listdir(cmp_structure_path)
            embed = discord.Embed(
                title = 'List of structure files on CMP'
            )
            for item in strucfiles:
                embed.add_field(name=item, value=get_size_format(get_file_size(cmp_structure_path + item)), inline=False)
            embed.set_footer(text='Chronos:tm:'),
            await ctx.send(embed=embed)

async def setup(bot): # a extension must have a setup function
    await bot.add_cog(structure(bot)) # adding a cog
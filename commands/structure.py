import discord
from discord.ext import commands
import os
import json

# reads config
f = open('config.json')
data = json.load(f)
cmp_structure_path = data['cmp_structure_path']
member_role = data['member_role']
f.close()

# function to get directory size
def get_file_size(file):
    """Returns the `directory` size in bytes"""
    total = 0
    try:
        # print("[+] Getting the size of", directory)
        for entry in os.scandir(file):
            if entry.is_file():
                # if it's a file, use stat() function
                total += entry.stat().st_size
            elif entry.is_dir():
                # if it's a directory, recursively call this function
                total += get_file_size(entry.path)
    except NotADirectoryError:
        # if `directory` isn't a directory, get the file size then
        return os.path.getsize(file)
    except PermissionError:
        # if for whatever reason we can't open the folder, return 0
        return 0
    return total

# function to make size human readable
def get_size_format(b, factor=1024, suffix="B"):
    """
    Scale bytes to its proper byte format
    e.g:
        1253656 => '1.20MB'
        1253656678 => '1.17GB'
    """
    for unit in ["", "K", "M", "G", "T", "P", "E", "Z"]:
        if b < factor:
            return f"{b:.2f}{unit}{suffix}"
        b /= factor
    return f"{b:.2f}Y{suffix}"

# structure command 
class structure(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    @commands.has_role(member_role)
    @commands.command(help = 'Download and upload structure files to/from cmp, Usage: `!!structure <download/upload/list> <structure name (Only used for download)>` (Member Only)')
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
        else:
                embed = discord.Embed(
                title = 'Invalid arguments'
                )
                embed.set_footer(text='Chronos™'),
                await ctx.send(embed=embed)

def setup(bot): # a extension must have a setup function
    bot.add_cog(structure(bot)) # adding a cog
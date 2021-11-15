import discord
from discord import client
from discord.ext import commands
import os

# ---CONFIG---
smp_path = '/root/servers/survival/server/world-smp0'
cmp_path = '/root/servers/creative/server/world-cmp0'
cmp2_path = '/root/servers/creative2/server/world-cmp0'
cmp3_path = '/root/servers/creative3/server/world-cmp0'
mirror_path = '/root/servers/mirror/server/world-mirror0'
# ---CONFIG---

def get_directory_size(directory):
    """Returns the `directory` size in bytes"""
    total = 0
    try:
        # print("[+] Getting the size of", directory)
        for entry in os.scandir(directory):
            if entry.is_file():
                # if it's a file, use stat() function
                total += entry.stat().st_size
            elif entry.is_dir():
                # if it's a directory, recursively call this function
                total += get_directory_size(entry.path)
    except NotADirectoryError:
        # if `directory` isn't a directory, get the file size then
        return os.path.getsize(directory)
    except PermissionError:
        # if for whatever reason we can't open the folder, return 0
        return 0
    return total

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

class worldsize(commands.Cog):
    def __init__(self, client):
        self.client = client
    @commands.command()
    async def worldsize(self, ctx):
        embed = discord.Embed(
            title = 'Chronos World Size',
        )
        embed.add_field(name='SMP', value=get_size_format(get_directory_size(smp_path)), inline=False)
        embed.add_field(name='CMP', value=get_size_format(get_directory_size(cmp_path)), inline=False)
        embed.add_field(name='CMP2', value=get_size_format(get_directory_size(cmp2_path)), inline=False)
        embed.add_field(name='CMP3', value=get_size_format(get_directory_size(cmp3_path)), inline=False)
        embed.add_field(name='MIRROR', value=get_size_format(get_directory_size(mirror_path)), inline=False)
        embed.set_footer(text='Chronos™'),
        await ctx.send(embed=embed)
    

def setup(bot): # a extension must have a setup function
	bot.add_cog(worldsize(client)) # adding a cog
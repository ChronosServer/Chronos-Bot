import discord
from discord import client
from discord.ext import commands
import os
import json

# reads config
f = open('config.json')
data = json.load(f)
server_name = data['server_name']
smp_path = data['smp_path']
cmp_path = data['cmp_path']
cmp2_path = data['cmp2_path']
cmp3_path = data['cmp3_path']
mirror_path = data['mirror_path']
pcrc_recordings_path = data['pcrc_recordings_path']
webserver_path = data['webserver_path']
f.close()

# function to get directory size
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

# worldsize command
class worldsize(commands.Cog):
    def __init__(self, client):
        self.client = client
    @commands.command(help = 'Display ' + server_name + ' world size, Usage `!!worldsize`')
    async def worldsize(self, ctx):
        embed = discord.Embed(
            title = server_name + ' World Size',
        )
        embed.add_field(name='SMP', value=get_size_format(get_directory_size(smp_path)), inline=False)
        embed.add_field(name='CMP', value=get_size_format(get_directory_size(cmp_path)), inline=False)
        embed.add_field(name='CMP2', value=get_size_format(get_directory_size(cmp2_path)), inline=False)
        embed.add_field(name='CMP3', value=get_size_format(get_directory_size(cmp3_path)), inline=False)
        embed.add_field(name='MIRROR', value=get_size_format(get_directory_size(mirror_path)), inline=False)
        embed.add_field(name='PCRC Recordings', value=get_size_format(get_directory_size(pcrc_recordings_path)), inline=False)
        embed.add_field(name='Webserver', value=get_size_format(get_directory_size(webserver_path)), inline=False)
        embed.set_footer(text='Chronos™'),
        await ctx.send(embed=embed)
    

def setup(bot): # a extension must have a setup function
	bot.add_cog(worldsize(client)) # adding a cog
import discord
from discord import app_commands
from discord.ext import commands
import json
import os
import sys
from typing import Literal
sys.path.append('./Chronos-Library/')
from filesizeLibrary import get_size_format, get_file_size
from confirmLibrary import confirm

f = open('config.json')
data = json.load(f)
webserver_path = os.path.join(data['server']['webserver_path'], 'webserver')
f.close()

VALID_EXTENSIONS = {'.zip', '.7z', '.rar', '.litematic', '.schematic', '.nbt', '.png', '.tar.gz'}


class webserver(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(
        name='webserver',
        description='Upload, delete or list files on the website file server (admin only)',
        help='Upload/delete/list files on the webserver, Usage: `!!webserver <upload/delete/list> <filename (delete only)>`, attach the file when uploading (Admin Only)'
    )
    @app_commands.describe(
        action='upload = add the attached file, delete = remove a file, list = show all files',
        filename='Name of the file to delete (delete only)',
        file='File to upload (upload only): .zip .7z .rar .litematic .schematic .nbt .png .tar.gz'
    )
    @app_commands.default_permissions(administrator=True)
    @commands.has_permissions(administrator=True)
    async def webserver(self, ctx, action: Literal['upload', 'delete', 'list'], filename: str = None, file: discord.Attachment = None):
        await ctx.defer()
        if action == 'upload':
            if file is None:
                await ctx.send(embed=discord.Embed(description='Attach the file you want to upload.').set_footer(text='Chronos™'))
                return
            if not any(file.filename.endswith(ext) for ext in VALID_EXTENSIONS):
                await ctx.send(embed=discord.Embed(description=f'Unsupported file type. Allowed: {", ".join(sorted(VALID_EXTENSIONS))}').set_footer(text='Chronos™'))
                return
            await file.save(fp=os.path.join(webserver_path, file.filename))
            embed = discord.Embed(title=f'{file.filename} has successfully been uploaded to the webserver')
            embed.add_field(name='Size', value=get_size_format(file.size), inline=False)
            embed.add_field(name='Link', value=f'https://www.chronosmc.com/files/webserver/{file.filename}', inline=False)
            embed.set_footer(text='Chronos™')
            await ctx.send(embed=embed)

        elif action == 'delete':
            if filename is None:
                await ctx.send(embed=discord.Embed(description='Please provide the name of the file to delete.').set_footer(text='Chronos™'))
                return
            if os.path.basename(filename) != filename:
                await ctx.send(embed=discord.Embed(description='Invalid file name.').set_footer(text='Chronos™'))
                return
            filepath = os.path.join(webserver_path, filename)
            if not os.path.isfile(filepath):
                await ctx.send(embed=discord.Embed(description=f'`{filename}` was not found on the webserver.').set_footer(text='Chronos™'))
                return
            size = get_size_format(get_file_size(filepath))
            if not await confirm(ctx, f"Delete `{filename}` ({size}) from the webserver? This can't be undone."):
                return
            os.remove(filepath)
            embed = discord.Embed(title=f'{filename} has been deleted', description=f'Filesize was {size}')
            embed.set_footer(text='Chronos™')
            await ctx.send(embed=embed)

        elif action == 'list':
            files = os.listdir(webserver_path)
            embed = discord.Embed(title='List of files on the webserver')
            for item in files:
                size = get_size_format(get_file_size(os.path.join(webserver_path, item)))
                embed.add_field(name=item, value=f'{size} — https://www.chronosmc.com/files/webserver/{item}', inline=False)
            embed.set_footer(text='Chronos™')
            await ctx.send(embed=embed)

    @webserver.autocomplete('filename')
    async def webserver_filename_autocomplete(self, interaction: discord.Interaction, current: str):
        if not os.path.isdir(webserver_path):
            return []
        names = sorted(os.listdir(webserver_path))
        return [app_commands.Choice(name=n, value=n) for n in names if current.lower() in n.lower() and len(n) <= 100][:25]


async def setup(bot):
    await bot.add_cog(webserver(bot))
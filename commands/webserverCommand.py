import discord
from discord.ext import commands
import json
import os
import sys
sys.path.append('./Chronos-Library/')
from filesizeLibrary import get_size_format, get_file_size

f = open('config.json')
data = json.load(f)
webserver_path = os.path.join(data['server']['webserver_path'], 'webserver')
f.close()

VALID_EXTENSIONS = {'.zip', '.7z', '.rar', '.litematic', '.schematic', '.nbt', '.png', '.tar.gz'}


class webserver(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(help='Upload/delete/list files on the webserver, Usage: `!!webserver <upload/delete/list> <filename (delete only)>` (Admin Only)')
    @commands.has_permissions(administrator=True)
    async def webserver(self, ctx, webaction, filename=None):
        if webaction == 'upload':
            if not ctx.message.attachments:
                return
            attachment = ctx.message.attachments[0]
            if not any(attachment.filename.endswith(ext) for ext in VALID_EXTENSIONS):
                return
            await attachment.save(fp=os.path.join(webserver_path, attachment.filename))
            embed = discord.Embed(title=f'{attachment.filename} has successfully been uploaded to the webserver')
            embed.add_field(name='Size', value=get_size_format(attachment.size), inline=False)
            embed.add_field(name='Link', value=f'https://www.chronosmc.com/files/webserver/{attachment.filename}', inline=False)
            embed.set_footer(text='Chronos™')
            await ctx.send(embed=embed)

        elif webaction == 'delete':
            filepath = os.path.join(webserver_path, filename)
            size = get_size_format(get_file_size(filepath))
            os.remove(filepath)
            embed = discord.Embed(title=f'{filename} has been deleted', description=f'Filesize was {size}')
            embed.set_footer(text='Chronos™')
            await ctx.send(embed=embed)

        elif webaction == 'list':
            files = os.listdir(webserver_path)
            embed = discord.Embed(title='List of files on the webserver')
            for item in files:
                size = get_size_format(get_file_size(os.path.join(webserver_path, item)))
                embed.add_field(name=item, value=f'{size} — https://www.chronosmc.com/files/webserver/{item}', inline=False)
            embed.set_footer(text='Chronos™')
            await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(webserver(bot))
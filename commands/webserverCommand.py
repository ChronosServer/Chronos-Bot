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
webserver_path = data['server']['webserver_path']
member_role = data['bot']['member_role']
f.close()

valid_file_extensions = ['.zip', '.7z', '.rar', '.litematic', '.schematic', '.nbt']

# structure command 
class webserver(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    @commands.command(help = 'Download and delete WDLs on  the webserver, Usage: `!!webserver <upload/delete/list> <filename (Only used for delete)>` (Admin Only)')
    @commands.has_permissions(administrator=True)
    async def webserver(self, ctx, webaction, filename2=None):
        if webaction == 'upload':
            if str(ctx.message.attachments) == "[]": # Checks if there is an attachment on the message
                return
            else: # If there is it gets the filename from message.attachments
                split_v1 = str(ctx.message.attachments).split("filename='")[1]
                filename = str(split_v1).split("' ")[0]
                for i in valid_file_extensions:
                    if filename.endswith(i): # Checks if it is a valid file
                        await ctx.message.attachments[0].save(fp=webserver_path + 'files/downloads/' + filename.format(filename)) # saves the file
                        embed = discord.Embed(
                            title = filename + ' has successfully been uploaded to the webserver'
                        )
                        embed.add_field(name='Size of the file is', value=get_size_format(ctx.message.attachments[0].size), inline=False)
                        embed.add_field(name='Link', value='https://www.chronosmc.com/files/downloads/' + filename, inline=False)
                        embed.set_footer(text='Chronos™'),
                        await ctx.send(embed=embed)
        elif webaction == 'delete':
            embed = discord.Embed(
                title = filename2 + ' has been deleted', description = 'Filesize of the WDL was ' + get_size_format(get_file_size(webserver_path + 'files/downloads/' + filename2))
            )
            embed.set_footer(text='Chronos™'),
            await ctx.send(embed=embed)
            os.remove(webserver_path + 'files/downloads/' + filename2)
        elif webaction == 'list':
            wdlfiles = os.listdir(webserver_path + 'files/downloads/')
            embed = discord.Embed(
                title = 'List of WDls on the webserver'
            )
            for item in wdlfiles:
                embed.add_field(name=item, value=str(get_size_format(get_file_size(webserver_path + 'files/downloads/' +  item)) + ' https://www.chronosmc.com/files/downloads/' + item), inline=False)
            embed.set_footer(text='Chronos™'),
            await ctx.send(embed=embed)

async def setup(bot): # a extension must have a setup function
    await bot.add_cog(webserver(bot)) # adding a cog
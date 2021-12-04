import discord
from discord.ext import commands
from discord import client
import json

# reads config
f = open('config.json')
data = json.load(f)
token = data['token']
prefix = data['prefix']
default_status = data['default_status']
f.close()

client = commands.Bot(command_prefix=prefix)

@client.event
async def on_ready():
    await client.change_presence(status=discord.Status.online,
                                 activity=discord.Game(name=default_status, type=discord.ActivityType.listening))


# put all of your cog files in here like 'worldsize'
# if you have a folder called 'commands' for example you could do #'commands.reloadclient'
cog_files = ['commands.worldsize',
             'commands.hardware',
             'commands.ping',
             'commands.setstatus',
             'commands.purge',
             'commands.ban',
             'commands.region',
             'commands.structure',
             'commands.execute',
             'commands.color'
            ]

for cog_file in cog_files: # cycle through the files in array
    client.load_extension(cog_file) # load the file
    print("%s has loaded." % cog_file) # print a success message.

# command to reload cogs
@client.command(help = 'Reload the bot, Usage: `!!reload` (Admin Only)')
@commands.has_permissions(administrator=True)
async def reload(ctx):
    for cog in cog_files:
        client.reload_extension(cog)
    else:
        embed = discord.Embed(
            title = 'Sucessfully reloaded the bot'
        )
        embed.set_footer(text='Chronos™'),
        await ctx.send(embed=embed)

# makes default help an embed
class NewHelpName(commands.MinimalHelpCommand):
    async def send_pages(self):
        destination = self.get_destination()
        for page in self.paginator.pages:
            emby = discord.Embed(description=page)
            await destination.send(embed=emby)
client.help_command = NewHelpName()

client.run(token) # runs the bot.
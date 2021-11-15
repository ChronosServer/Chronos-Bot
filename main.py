import discord
from discord.ext import commands
from discord import client

#---config---
token = 'your.token.goes.here' #bot token here
prefix = '!!' #prefix you want to use for the bot

client = commands.Bot(command_prefix=prefix)

@client.event
async def on_ready():
    await client.change_presence(status=discord.Status.online,
                                 activity=discord.Game(name='Chronos™', type=discord.ActivityType.listening))


# put all of your cog files in here like 'worldsize'
# if you have a folder called 'commands' for example you could do #'commands.reloadclient'
cog_files = ['commands.worldsize',
             'commands.hardware',
             'commands.ping'
            ]

for cog_file in cog_files: # cycle through the files in array
    client.load_extension(cog_file) # load the file
    print("%s has loaded." % cog_file) # print a success message.

@client.command()
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

class NewHelpName(commands.MinimalHelpCommand):
    async def send_pages(self):
        destination = self.get_destination()
        for page in self.paginator.pages:
            emby = discord.Embed(description=page)
            await destination.send(embed=emby)
client.help_command = NewHelpName()

client.run(token) # runs the bot.
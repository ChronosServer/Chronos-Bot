import discord
from discord.ext import commands

#---config---
token = 'your.token.goes.here' #bot token here
prefix = '!!' #prefix you want to use for the bot

client = commands.Bot(command_prefix=prefix)

@client.event
async def on_ready():
    await client.change_presence(status=discord.Status.online,
                                 activity=discord.Game(name='Playing on Chronos', type=discord.ActivityType.listening))


# put all of your cog files in here like 'worldsize'
# if you have a folder called 'commands' for example you could do #'commands.worldsize'
cog_files = ['commands.worldsize']

for cog_file in cog_files: # cycle through the files in array
    client.load_extension(cog_file) # load the file
    print("%s has loaded." % cog_file) # print a success message.

client.run(token) # runs the bot.
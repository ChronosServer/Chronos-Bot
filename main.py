import discord
from discord.ext import commands
from discord import client
import json
import os

# reads config
f = open('config.json')
data = json.load(f)
token = data['bot']['token']
prefix = data['bot']['prefix']
default_status = data['bot']['default_status']
f.close()

image_archive_id = 922209226170986556

client = commands.Bot(command_prefix=prefix, case_insensitive=True)

@client.event
async def on_ready():
    await client.change_presence(status=discord.Status.online,
                                 activity=discord.Game(name=default_status, type=discord.ActivityType.listening))

for filename in os.listdir('./commands'):
  if filename.endswith('.py'):
    client.load_extension(f'commands.{filename[:-3]}')
    print(f'commands.{filename[:-3]} has loaded.')
  else:
    print(f'Unable to load ' + filename)
print('All extensions have been loaded')

@client.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.errors.CommandNotFound):
        error_msg = 'Command not found'
    elif isinstance(error, commands.errors.CheckFailure):
        error_msg = 'Insufficient permission'
    elif isinstance(error, commands.errors.UserInputError):
        error_msg = 'Invalid usage'
    else:
        raise error
    embed = discord.Embed(
      description = error_msg
    )
    embed.set_footer(text='Chronos™'),
    await ctx.send(embed=embed)

# command to reload cogs
@client.command(help = 'Reload the bot, Usage: `!!reload` (Admin Only)')
@commands.has_permissions(administrator=True)
async def reload(ctx):
  for filename in os.listdir('./commands'):
    if filename.endswith('.py'):
      client.reload_extension(f'commands.{filename[:-3]}')
      print(f'commands.{filename[:-3]} reloaded.')
    else:
      print(f'Unable to reload ' + filename)
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
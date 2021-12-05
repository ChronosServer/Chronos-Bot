import discord
from discord.ext import commands
from discord import client
import json
import os

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

for filename in os.listdir('./commands'):
  if filename.endswith('.py'):
    client.load_extension(f'commands.{filename[:-3]}')
    print(f'commands.{filename[:-3]} has loaded.')
  else:
    print(f'Unable to load ' + filename)
print('All extensions have been loaded')

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
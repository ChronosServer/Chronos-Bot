import discord
import discord.utils
from discord.ext import commands
from discord import client
import json
import os
import asyncio
import nest_asyncio
from datetime import datetime, timezone
nest_asyncio.apply()

# reads config
f = open('config.json')
data = json.load(f)
token = data['bot']['token']
prefix = data['bot']['prefix']
default_status = data['bot']['default_status']
crash_report_paths = {
    k.replace('_path', '').replace('_', '').upper(): os.path.join(v.rstrip('/'), 'crash-reports')
    for k, v in data['server'].items()
    if k.endswith('_path') and k.replace('_path', '_world_name') in data['server']
}
crash_report_log_id = int(data['bot']['crash_report_log_id'])
f.close()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
client = commands.Bot(command_prefix=prefix, case_insensitive=True, intents=intents)

async def watch_folders():
    seen = {}
    for label, folder in crash_report_paths.items():
        try:
            seen[folder] = set(os.listdir(folder))
        except FileNotFoundError:
            print(f"Warning: folder not found: {folder} ({label})")
            seen[folder] = set()

    while True:
        for label, folder in crash_report_paths.items():
            try:
                current = set(os.listdir(folder))
            except FileNotFoundError:
                continue
            new_files = current - seen[folder]
            for filename in new_files:
                await send_crash_alert(folder, filename, label)
            seen[folder] = current
        await asyncio.sleep(5)


async def send_crash_alert(folder, filename, server_name):
    channel = client.get_channel(crash_report_log_id)
    if channel is None:
        print(f"Could not find channel {crash_report_log_id}")
        return

    filepath = os.path.join(folder, filename)
    timestamp = int(datetime.now(timezone.utc).timestamp())

    embed = discord.Embed(
        title=f"💥 Crash Report — {server_name}",
        description=f"**File:** `{filename}`\n**Detected:** <t:{timestamp}:F> (<t:{timestamp}:R>)",
        color=discord.Color.red()
    )
    embed.set_footer(text="Chronos™")

    try:
        await channel.send(embed=embed, file=discord.File(filepath))
    except Exception as e:
        print(f"Failed to send crash alert: {e}")

@client.event
async def on_ready():
    await client.change_presence(status=discord.Status.online, activity=discord.Game(name=default_status, type=discord.ActivityType.listening))
    print(f"Crash report map: {crash_report_paths}")
    print(f"Crash log channel: {client.get_channel(crash_report_log_id)}")

async def load_extensions():
    for filename in os.listdir('./commands'):
        if filename.endswith('.py'):
            await client.load_extension(f'commands.{filename[:-3]}')
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
    elif isinstance(error, commands.errors.CommandInvokeError):
        raise error.original  # re-raise the underlying exception
    else:
        raise error
    embed = discord.Embed(description=error_msg)
    embed.set_footer(text='Chronos™')
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
    embed = discord.Embed(title='Sucessfully reloaded the bot')
    embed.set_footer(text='Chronos™'),
    await ctx.send(embed=embed)

class NewHelpName(commands.MinimalHelpCommand):
    async def send_pages(self):
        destination = self.get_destination()
        for page in self.paginator.pages:
            emby = discord.Embed(description=page)
            await destination.send(embed=emby)
client.help_command = NewHelpName()

async def main():
    async with client:
        await load_extensions()
        client.loop.create_task(watch_folders())
        await client.run(token)

asyncio.run(main())
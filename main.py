import discord
import discord.utils
from discord import app_commands
from discord.ext import commands
from discord import client
import json
import os
import asyncio
import traceback
from datetime import datetime, timezone

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

GUILD = discord.Object(id=int(data['bot']['guild_id']))

def flag(name, default=True):
    value = data['bot'].get(name, default)
    return value if isinstance(value, bool) else str(value).strip().lower() in ('true', '1', 'yes', 'on')

# which ways of running a command are enabled (config.json -> bot)
use_slash = flag('slash_commands')            # /command
use_mention_prefix = flag('mention_prefix')   # @Chronos command
use_text_prefix = flag('text_prefix')         # !!command, the only one that needs the Message Content intent

if not (use_slash or use_mention_prefix or use_text_prefix):
    print('Warning: slash_commands, mention_prefix and text_prefix are all false, nothing can run a command')

intents = discord.Intents.default()
intents.message_content = use_text_prefix
intents.members = True

if use_text_prefix and use_mention_prefix:
    command_prefix = commands.when_mentioned_or(prefix)
elif use_text_prefix:
    command_prefix = prefix
else:
    command_prefix = commands.when_mentioned
client = commands.Bot(command_prefix=command_prefix, case_insensitive=True, intents=intents)

if not (use_text_prefix or use_mention_prefix):
    @client.event
    async def on_message(message):
        pass  # no text commands at all, so don't even look at messages

async def sync_commands():
    # slash commands are registered to the Chronos guild only (instant, no global propagation delay)
    client.tree.clear_commands(guild=GUILD)
    if use_slash:
        client.tree.copy_global_to(guild=GUILD)
    await client.tree.sync(guild=GUILD)  # with slash off this pushes an empty list, which removes the old slash commands

@client.event
async def setup_hook():
    await sync_commands()

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
            # one bad folder (permissions, a disk error) must never stop the watcher:
            # an exception here would end the task for good, without a word
            try:
                current = set(os.listdir(folder))
            except FileNotFoundError:
                continue
            except Exception as e:
                print(f"Crash watcher: can't list {folder} ({label}): {e!r}")
                continue
            new_files = current - seen[folder]
            for filename in new_files:
                try:
                    await send_crash_alert(folder, filename, label)
                except Exception as e:
                    print(f"Crash watcher: alert for {filename} ({label}) failed: {e!r}")
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
        size = os.path.getsize(filepath)
    except OSError:
        size = None
    limit = channel.guild.filesize_limit if getattr(channel, 'guild', None) else 10 * 1024 * 1024
    if size is not None and size <= limit:
        try:
            await channel.send(embed=embed, file=discord.File(filepath))
            return
        except Exception as e:
            print(f"Failed to send crash alert with the file: {e}")
    # too big to upload (or the upload failed): the alert itself still goes out
    reason = 'too big to upload' if size is not None and size > limit else "couldn't be attached"
    embed.description += f"\n**The file {reason}**, it is on the server: `{filepath}`"
    try:
        await channel.send(embed=embed)
    except Exception as e:
        print(f"Failed to send crash alert: {e}")

@client.event
async def on_ready():
    await client.change_presence(status=discord.Status.online, activity=discord.Game(name=default_status))  # shows "Playing <status>" (owner: keep it)
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

def _unwrap(error):
    # text commands: CommandInvokeError(original); slash (hybrid):
    # HybridCommandError(app_commands.CommandInvokeError(original))
    while getattr(error, 'original', None) is not None:
        error = error.original
    return error


@client.event
async def on_command_error(ctx, error):
    original = _unwrap(error)
    if isinstance(error, commands.errors.CommandNotFound):
        error_msg = 'Command not found'
    elif isinstance(error, commands.errors.CheckFailure):
        error_msg = 'Insufficient permission'
    elif isinstance(error, commands.errors.UserInputError) or isinstance(original, app_commands.TransformerError):
        error_msg = 'Invalid usage'
    else:
        # Anything else is a bug or an unreachable server. Always answer: a
        # slash command that never replies stays on "thinking..." forever.
        print(f"Command {ctx.command} failed:")
        traceback.print_exception(type(original), original, original.__traceback__)
        error_msg = f'Something went wrong (`{type(original).__name__}`). The details are in the bot console.'
    embed = discord.Embed(description=error_msg)
    embed.set_footer(text='Chronos™')
    try:
        await ctx.send(embed=embed)
    except discord.HTTPException as e:
        print(f"Couldn't send the error reply: {e}")

# command to reload cogs
@client.hybrid_command(name='reload', description='Reload the bot (admin only)', help='Reload the bot, Usage: `!!reload` (Admin Only)')
@commands.has_permissions(administrator=True)
async def reload(ctx):
    await ctx.defer()
    for filename in os.listdir('./commands'):
        if filename.endswith('.py'):
            await client.reload_extension(f'commands.{filename[:-3]}')
            print(f'commands.{filename[:-3]} reloaded.')
        else:
            print(f'Unable to reload ' + filename)
    await sync_commands()
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
    # client.start() runs inside this event loop. client.run() would start its own loop,
    # which is what nest_asyncio used to paper over; nest_asyncio breaks on Python 3.14.
    async with client:
        await load_extensions()
        watcher = asyncio.create_task(watch_folders())  # keep a reference so it isn't garbage-collected
        await client.start(token)

asyncio.run(main())
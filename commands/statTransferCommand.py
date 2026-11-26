import asyncio
import discord
from discord import app_commands
from discord.ext import commands
import json
import os
import socket
import sys
sys.path.append('./Chronos-Library/')
from confirmLibrary import confirm
from nbt import nbt
from rcon import Client

f = open('config.json')
data = json.load(f)
smp_rcon_port = data['server']['smp_rcon_port']
smp_path = data['server']['smp_path']
smp_world_name = data['server']['smp_world_name']
rcon_pass = data['server']['rcon_pass']
f.close()

SCOREBOARD_PATH = os.path.join(smp_path, smp_world_name, 'data', 'minecraft', 'scoreboard.dat')


def read_objectives():
    """Every objective name on the SMP board. Paper leaves the Objectives tag
    out when there are none (missing tag = empty list)."""
    board = nbt.NBTFile(SCOREBOARD_PATH)['data']
    if 'Objectives' not in board:
        return []
    return [o['Name'].value for o in board['Objectives']]


def run_commands(cmds):
    """Runs the commands over ONE RCON connection (thousands of them: one
    connection each was slow). Blocking, so the command runs it in a thread.
    Returns (number run, error text or None); stops at the first error."""
    done = 0
    try:
        with Client('127.0.0.1', int(smp_rcon_port), passwd=rcon_pass, timeout=5) as client:
            for cmd in cmds:
                client.run(cmd)
                done += 1
    except socket.timeout:
        return done, "the SMP didn't answer in time"
    except ConnectionError:
        return done, 'the SMP is offline or RCON is unavailable'
    except Exception as e:
        return done, f'{type(e).__name__}: {e}'
    return done, None


def footer(embed):
    embed.set_footer(text='Chronos™')
    return embed


class statTransfer(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(
        name='stattransfer',
        description='Move all scoreboard stats from one player to another (admin only)',
        help='Transfer statistics between players, Usage: `!!statTransfer <from_player> <to_player>` (Admin Only)'
    )
    @app_commands.describe(
        from_player='Minecraft username to take the stats from (their scores are reset afterwards)',
        to_player='Minecraft username to add the stats to'
    )
    @app_commands.default_permissions(administrator=True)
    @commands.has_permissions(administrator=True)
    async def statTransfer(self, ctx, from_player: str, to_player: str):
        await ctx.defer()
        if from_player.lower() == to_player.lower():
            await ctx.send(embed=footer(discord.Embed(description='The two players are the same, nothing to transfer.')))
            return
        if any(c.isspace() for c in from_player + to_player):
            await ctx.send(embed=footer(discord.Embed(description='Player names cannot contain spaces.')))
            return
        if not await confirm(ctx, f"Move **all** scoreboard stats from `{from_player}` to `{to_player}`?\nThe scores of `{from_player}` are reset afterwards. This can't be undone."):
            return

        # reading the big scoreboard.dat and running ~9k RCON commands both block,
        # so they run in threads (on the event loop they froze the whole bot)
        objectives = await asyncio.to_thread(read_objectives)
        if not objectives:
            await ctx.send(embed=footer(discord.Embed(description='The SMP scoreboard has no objectives, nothing to transfer.')))
            return
        cmds = [f'scoreboard players operation {to_player} {name} += {from_player} {name}' for name in objectives]
        done, err = await asyncio.to_thread(run_commands, cmds)
        if err:
            # the old name is NOT reset: the transferred part would be lost otherwise
            await ctx.send(embed=footer(discord.Embed(description=(
                f'Transfer stopped after {done:,} of {len(cmds):,} scoreboards: {err}.\n'
                f'`{from_player}` was **not** reset. Running the command again would add the '
                f'first {done:,} scoreboards twice; fix them by hand or ask for help first.'))))
            return

        await asyncio.sleep(5)
        done, err = await asyncio.to_thread(run_commands, [f'scoreboard players reset {from_player}'])
        if err:
            await ctx.send(embed=footer(discord.Embed(description=(
                f'All {len(cmds):,} scoreboards were added to `{to_player}`, but resetting '
                f'`{from_player}` failed: {err}. Run `scoreboard players reset {from_player}` by hand.'))))
            return
        await ctx.send(embed=footer(discord.Embed(
            description=f'Statistics transferred from {from_player} to {to_player} ({len(cmds):,} scoreboards)')))


async def setup(bot):
    await bot.add_cog(statTransfer(bot))

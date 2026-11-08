import asyncio
import discord
from discord import app_commands
from discord.ext import commands
import json
import os
import sys
sys.path.append('./Chronos-Library/')
from defaultRconLibrary import defaultRcon
from confirmLibrary import confirm
from nbt import nbt

f = open('config.json')
data = json.load(f)
smp_rcon_port = data['server']['smp_rcon_port']
smp_path = data['server']['smp_path']
smp_world_name = data['server']['smp_world_name']
rcon_pass = data['server']['rcon_pass']
f.close()

SCOREBOARD_PATH = os.path.join(smp_path, smp_world_name, 'data', 'minecraft', 'scoreboard.dat')

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
            await ctx.send(embed=discord.Embed(description='The two players are the same, nothing to transfer.').set_footer(text='Chronos™'))
            return
        if not await confirm(ctx, f"Move **all** scoreboard stats from `{from_player}` to `{to_player}`?\nThe scores of `{from_player}` are reset afterwards. This can't be undone."):
            return
        scoreboards = nbt.NBTFile(SCOREBOARD_PATH)["data"]
        for scoreboard in scoreboards["Objectives"]:
            name = scoreboard["Name"].value
            await defaultRcon(smp_rcon_port, rcon_pass, f"/scoreboard players operation {to_player} {name} += {from_player} {name}")
        await asyncio.sleep(5)
        await defaultRcon(smp_rcon_port, rcon_pass, f"/scoreboard players reset {from_player}")
        embed = discord.Embed(description=f'Statistics transferred from {from_player} to {to_player}')
        embed.set_footer(text='Chronos™')
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(statTransfer(bot))
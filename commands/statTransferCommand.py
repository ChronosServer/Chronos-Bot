import discord
from discord.ext import commands
import json
import os
import time
import sys
sys.path.append('./Chronos-Library/')
from defaultRconLibrary import defaultRcon
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

    @commands.command(help='Transfer statistics between players, Usage: `!!statTransfer <from> <to>` (Admin Only)')
    @commands.has_permissions(administrator=True)
    async def statTransfer(self, ctx, playerf, playert):
        scoreboards = nbt.NBTFile(SCOREBOARD_PATH)["data"]
        for scoreboard in scoreboards["Objectives"]:
            name = scoreboard["Name"].value
            await defaultRcon(smp_rcon_port, rcon_pass, f"/scoreboard players operation {playert} {name} += {playerf} {name}")
        time.sleep(5)
        await defaultRcon(smp_rcon_port, rcon_pass, f"/scoreboard players reset {playerf}")
        embed = discord.Embed(description=f'Statistics transferred from {playerf} to {playert}')
        embed.set_footer(text='Chronos™')
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(statTransfer(bot))
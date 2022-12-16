import discord
from discord.ext import commands
from rcon import Client
import json
import socket
import errno
from nbt import nbt
import time
import sys
sys.path.append('./Chronos-Library/')
from defaultRconLibrary import defaultRcon

# reads config
f = open('config.json')
data = json.load(f)
smp_rcon_port = data['server']['smp_rcon_port']
smp_path = data['server']['smp_path']
rcon_pass = data['server']['rcon_pass']
f.close()

# execute command
class statTransfer(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    @commands.command(help = 'Transfer statistics beetween players, Usage: `!!statTransfer <player you are transfering from> <player to transfer to>` (Admin Only)')
    @commands.has_permissions(administrator=True)
    async def statTransfer(self, ctx, playerf, playert):
        scoreboards = nbt.NBTFile(smp_path + "world-smp0/data/scoreboard.dat")["data"]
        for scoreboard in scoreboards["Objectives"]:
            await defaultRcon(smp_rcon_port, rcon_pass, "/scoreboard players operation " + playert + " " + scoreboard["Name"].value + " += " + playerf + " " + scoreboard["Name"].value)
        time.sleep(int("5"))
        await defaultRcon(smp_rcon_port, rcon_pass, "/scoreboard players reset " + playerf)
        embed = discord.Embed(
            description = 'Statistics have been transfered from ' + playerf + ' to ' + playert,
        )
        await ctx.send(embed=embed)

def setup(bot): # a extension must have a setup function
	bot.add_cog(statTransfer(bot)) # adding a cog
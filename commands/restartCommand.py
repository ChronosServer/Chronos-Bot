from discord.ext import commands
import json
import os
import time
import sys
sys.path.append('./Chronos-Library/')
from restartLibrary import rcon
from restartLibrary import handle_list
from restartLibrary import read_log

# reads config
f = open('config.json')
data = json.load(f)
max_restart_time = data['server']['max_restart_time']
smp_rcon_port = data['server']['smp_rcon_port']
cmp_rcon_port = data['server']['cmp_rcon_port']
cmp2_rcon_port = data['server']['cmp2_rcon_port']
cmp3_rcon_port = data['server']['cmp3_rcon_port']
cmp4_rcon_port = data['server']['cmp4_rcon_port']
mirror_rcon_port = data['server']['mirror_rcon_port']
snapshot_rcon_port = data['server']['snapshot_rcon_port']
building_rcon_port = data['server']['building_rcon_port']
smp_path = data['server']['smp_path']
cmp_path = data['server']['cmp_path']
cmp2_path = data['server']['cmp2_path']
cmp3_path = data['server']['cmp3_path']
cmp4_path = data['server']['cmp4_path']
mirror_path = data['server']['mirror_path']
snapshot_path = data['server']['snapshot_path']
building_path = data['server']['building_path']
rcon_pass = data['server']['rcon_pass']
f.close()

# restart command
class restart(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    @commands.command(help = 'Restart servers, Usage: `!!restart <server>` -bot (optinal if you want to reload bots) (Admin Only)')
    @commands.has_permissions(administrator=True)
    async def restart(self, ctx, server, reload_status=None):
        if server == 'smp':
            rcon_port = smp_rcon_port
            server_path = smp_path
        if server == 'cmp':
            rcon_port = cmp_rcon_port
            server_path = cmp_path
        if server == 'cmp2':
            rcon_port = cmp2_rcon_port
            server_path = cmp2_path
        if server == 'cmp3':
            rcon_port = cmp3_rcon_port
            server_path = cmp3_path
        if server == 'mirror':
            rcon_port = mirror_rcon_port
            server_path = mirror_path
        if server == 'snapshot':
            rcon_port = snapshot_rcon_port
            server_path = snapshot_path
        if server == 'building':
            rcon_port = building_rcon_port
            server_path = building_path

        if reload_status == '-bot':
            with open(os.path.join(str(server_path), 'whitelist.json'), 'r') as f:
                whitelist = json.load(f)
                players = [player["name"] for player in whitelist]
                f.close()
            player_list = handle_list(rcon(rcon_port, rcon_pass, 'list'))
            fake_players = []
# get fake players
            for player in player_list:
                if player not in players:
                    fake_players.append(player)
                else:
                    pass
# get real players
            real_players = []
            for player in set(player_list) - set(fake_players):
                real_players.append(player)
# get dimension list of players
            rcon(rcon_port, rcon_pass, 'execute in the_end run say @a[distance=0..]')
            time.sleep(0.05)
            end_players = read_log(server_path)
            end_fake_players = []
            for end_player in set(end_players) - set(real_players):
                end_fake_players.append(end_player)
            rcon(rcon_port, rcon_pass, 'execute in overworld run say @a[distance=0..]')
            time.sleep(0.05)
            ow_players = read_log(server_path)
            ow_fake_players = []
            for ow_player in set(ow_players) - set(real_players):
                ow_fake_players.append(ow_player)
            rcon(rcon_port, rcon_pass, 'execute in the_nether run say @a[distance=0..]')
            time.sleep(0.05)
            nether_players = read_log(server_path)
            nether_fake_players = []
            for nether_player in set(nether_players) - set(real_players):
                nether_fake_players.append(nether_player)
# get gamemode list of players
            rcon(rcon_port, rcon_pass, 'say @a[gamemode=survival]')
            time.sleep(0.05)
            survival_players = read_log(server_path)
            survival_fake_players = []
            for survival_player in set(survival_players) - set(real_players):
                survival_fake_players.append(survival_player)
            rcon(rcon_port, rcon_pass, 'say @a[gamemode=creative]')
            time.sleep(0.05)
            creative_players = read_log(server_path)
            creative_fake_players = []
            for creative_player in set(creative_players) - set(real_players):
                creative_fake_players.append(creative_player)
            rcon(rcon_port, rcon_pass, 'say @a[gamemode=spectator]')
            time.sleep(0.05)
            spectator_players = read_log(server_path)
            spectator_fake_players = []
            for spectator_player in set(spectator_players) - set(real_players):
                spectator_fake_players.append(spectator_player)
            rcon(rcon_port, rcon_pass, 'say @a[gamemode=adventure]')
            time.sleep(0.05)
            adventure_players = read_log(server_path)
            adventure_fake_players = []
            for adventure_player in set(adventure_players) - set(real_players):
                adventure_fake_players.append(adventure_player)

            fake_player_reload_commands = []
# get command for reloading fake players
            for fake_player in fake_players:
                coords = rcon(rcon_port, rcon_pass, 'execute at ' + fake_player + ' run tp ' + fake_player + ' ~ ~ ~')
                coords = coords.replace(',', '')
                coords = coords.replace('Teleported ' + fake_player + ' to ', '')
                rot0 = rcon(rcon_port, rcon_pass, 'execute as ' + fake_player + ' run data get entity ' + fake_player + ' Rotation[0] 1')
                rot0 = rot0.replace('Rotation[0] on ' + fake_player + ' after scale factor of 1.00 is ', '')
                rot1 = rcon(rcon_port, rcon_pass, 'execute as ' + fake_player + ' run data get entity ' + fake_player + ' Rotation[1] 1')
                rot1 = rot1.replace('Rotation[1] on ' + fake_player + ' after scale factor of 1.00 is ', '')
                if fake_player in end_fake_players:
                    dim = 'minecraft:the_end'
                if fake_player in ow_fake_players:
                    dim = 'minecraft:overworld'
                if fake_player in nether_fake_players:
                    dim = 'minecraft:the_nether'
                if fake_player in survival_fake_players:
                    gamemode = 'survival'
                if fake_player in creative_fake_players:
                    gamemode = 'creative'
                if fake_player in spectator_fake_players:
                    gamemode = 'spectator'
                if fake_player in adventure_fake_players:
                    gamemode = 'adventure'
                fake_player_reload_commands.append('/player ' + fake_player + ' spawn at ' + coords + ' facing ' + rot0 + ' ' + rot1 + ' in ' + dim + ' in ' + gamemode)
#kick real players and restart server        
            for real_player in real_players:
                rcon(rcon_port, rcon_pass, 'kick ' + real_player + ' SERVER RESTARTING')
            time.sleep(2.5)
            rcon(rcon_port, rcon_pass, 'stop')
# reload bots
            time.sleep(float(max_restart_time))
            for command in fake_player_reload_commands:
                rcon(rcon_port, rcon_pass, command)
# restart without reloading bots
        if reload_status != '-bot':
            rcon(rcon_port, rcon_pass, 'kick @a')
            time.sleep(2.5)
            rcon(rcon_port, rcon_pass, 'stop')

async def setup(bot): # a extension must have a setup function
	await bot.add_cog(restart(bot)) # adding a cog

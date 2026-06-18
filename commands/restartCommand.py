from discord.ext import commands
import json
import os
import time
import sys
sys.path.append('./Chronos-Library/')
from restartLibrary import rcon, handle_list, read_log

f = open('config.json')
data = json.load(f)
max_restart_time = float(data['server']['max_restart_time'])
rcon_pass = data['server']['rcon_pass']
server_cfg = data['server']

RCON_PORTS = {
    k.replace('_rcon_port', ''): v
    for k, v in server_cfg.items()
    if k.endswith('_rcon_port')
}
SERVER_PATHS = {
    k.replace('_path', ''): v
    for k, v in server_cfg.items()
    if k.endswith('_path') and not k.endswith('_world_name')
}
f.close()

DIMENSIONS = ['the_end', 'overworld', 'the_nether']
DIM_LABEL = {
    'the_end':    'minecraft:the_end',
    'overworld':  'minecraft:overworld',
    'the_nether': 'minecraft:the_nether',
}
GAMEMODES = ['survival', 'creative', 'spectator', 'adventure']


class restart(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(help='Restart servers, Usage: `!!restart <server>` `-bot` (optional, reloads bots) (Admin Only)')
    @commands.has_permissions(administrator=True)
    async def restart(self, ctx, server, reload_status=None):
        rcon_port = RCON_PORTS.get(server)
        server_path = SERVER_PATHS.get(server)
        if rcon_port is None or server_path is None:
            await ctx.send(f"Unknown server `{server}`. Valid: {', '.join(RCON_PORTS)}")
            return

        if reload_status != '-bot':
            rcon(rcon_port, rcon_pass, 'kick @a')
            time.sleep(2.5)
            rcon(rcon_port, rcon_pass, 'stop')
            return

        with open(os.path.join(server_path, 'whitelist.json'), 'r') as f:
            whitelist = json.load(f)
        whitelisted = {p['name'] for p in whitelist}

        player_list = handle_list(rcon(rcon_port, rcon_pass, 'list')) or []
        fake_players = [p for p in player_list if p not in whitelisted]
        real_players = [p for p in player_list if p in whitelisted]

        fake_player_dims = {}
        for dim in DIMENSIONS:
            rcon(rcon_port, rcon_pass, f'execute in {dim} run say @a[distance=0..]')
            time.sleep(0.05)
            in_dim = read_log(server_path)
            for fp in fake_players:
                if fp in in_dim and fp not in fake_player_dims:
                    fake_player_dims[fp] = DIM_LABEL[dim]

        fake_player_modes = {}
        for gm in GAMEMODES:
            rcon(rcon_port, rcon_pass, f'say @a[gamemode={gm}]')
            time.sleep(0.05)
            in_gm = read_log(server_path)
            for fp in fake_players:
                if fp in in_gm and fp not in fake_player_modes:
                    fake_player_modes[fp] = gm

        fake_player_reload_commands = []
        for fp in fake_players:
            coords_raw = rcon(rcon_port, rcon_pass, f'execute at {fp} run tp {fp} ~ ~ ~')
            rot0_raw   = rcon(rcon_port, rcon_pass, f'execute as {fp} run data get entity {fp} Rotation[0] 1')
            rot1_raw   = rcon(rcon_port, rcon_pass, f'execute as {fp} run data get entity {fp} Rotation[1] 1')

            if not all([coords_raw, rot0_raw, rot1_raw]):
                print(f"Skipping {fp}: couldn't get position/rotation data")
                continue

            coords = coords_raw.replace(',', '').replace(f'Teleported {fp} to ', '')
            rot0   = rot0_raw.replace(f'Rotation[0] on {fp} after scale factor of 1.00 is ', '')
            rot1   = rot1_raw.replace(f'Rotation[1] on {fp} after scale factor of 1.00 is ', '')
            dim    = fake_player_dims.get(fp, 'minecraft:overworld')
            gm     = fake_player_modes.get(fp, 'survival')
            fake_player_reload_commands.append(
                f'/player {fp} spawn at {coords} facing {rot0} {rot1} in {dim} in {gm}'
            )

        for rp in real_players:
            rcon(rcon_port, rcon_pass, f'kick {rp} SERVER RESTARTING')
        time.sleep(2.5)
        rcon(rcon_port, rcon_pass, 'stop')

        time.sleep(max_restart_time)
        for command in fake_player_reload_commands:
            rcon(rcon_port, rcon_pass, command)


async def setup(bot):
    await bot.add_cog(restart(bot))
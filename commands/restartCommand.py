import asyncio
import discord
from discord import app_commands
from discord.ext import commands
import json
import os
import sys
from typing import Literal
sys.path.append('./Chronos-Library/')
from restartLibrary import rcon, handle_list, read_log
from confirmLibrary import confirm

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

SERVERS = Literal[tuple(s for s in RCON_PORTS if s in SERVER_PATHS)]

DIMENSIONS = ['the_end', 'overworld', 'the_nether']
DIM_LABEL = {
    'the_end':    'minecraft:the_end',
    'overworld':  'minecraft:overworld',
    'the_nether': 'minecraft:the_nether',
}
GAMEMODES = ['survival', 'creative', 'spectator', 'adventure']
COME_BACK_POLL_S = 10  # after max_restart_time: check every 10 s whether it is up


async def arcon(port, cmd):
    # RCON blocks (up to 1.5 s per call): in a thread, never on the event loop
    return await asyncio.to_thread(rcon, port, rcon_pass, cmd)


async def stop_server(port):
    # the server may close the connection while answering 'stop': that is the
    # stop working, not an error (it used to end the command before the respawn)
    try:
        await arcon(port, 'stop')
    except OSError:
        pass


async def wait_until_up(port, extra_s):
    """True once the server answers RCON again, checking every
    COME_BACK_POLL_S for up to extra_s seconds."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + extra_s
    while True:
        try:
            if await arcon(port, 'list') is not None:
                return True
        except Exception:
            pass  # still starting up (connection reset, ...)
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(COME_BACK_POLL_S)


class restart(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(
        name='restart',
        description='Restart a Minecraft server, optionally respawning its bots (admin only)',
        help='Restart servers, Usage: `!!restart <server> [reload_bots]`, set `reload_bots` to `true` to respawn bots after the restart (Admin Only)'
    )
    @app_commands.describe(
        server='Minecraft server to restart',
        reload_bots="Respawn the server's bots (fake players) after the restart (waits for it to come back up)"
    )
    @app_commands.default_permissions(administrator=True)
    @commands.has_permissions(administrator=True)
    async def restart(self, ctx, server: SERVERS, reload_bots: bool = False):
        await ctx.defer()
        rcon_port = RCON_PORTS[server]
        server_path = SERVER_PATHS[server]

        action = f'Restart `{server}`' + (' and respawn its bots afterwards' if reload_bots else '')
        if not await confirm(ctx, f'{action}? Everyone on it will be kicked.'):
            return

        # 'list' doubles as the online check: None = no RCON answer
        list_response = await arcon(rcon_port, 'list')
        if list_response is None:
            await ctx.send(embed=discord.Embed(
                description=f"`{server}` doesn't answer RCON (offline?), nothing was done."
            ).set_footer(text='Chronos™'))
            return

        if not reload_bots:
            await ctx.send(embed=discord.Embed(description=f'Restarting `{server}`...').set_footer(text='Chronos™'))
            await arcon(rcon_port, 'kick @a')
            await asyncio.sleep(2.5)
            await stop_server(rcon_port)
            return

        with open(os.path.join(server_path, 'whitelist.json'), 'r') as f:
            whitelist = json.load(f)
        whitelisted = {p['name'] for p in whitelist}

        player_list = handle_list(list_response) or []
        fake_players = [p for p in player_list if p not in whitelisted]
        real_players = [p for p in player_list if p in whitelisted]

        fake_player_dims = {}
        for dim in DIMENSIONS:
            await arcon(rcon_port, f'execute in {dim} run say @a[distance=0..]')
            await asyncio.sleep(0.05)
            in_dim = read_log(server_path)
            for fp in fake_players:
                if fp in in_dim and fp not in fake_player_dims:
                    fake_player_dims[fp] = DIM_LABEL[dim]

        fake_player_modes = {}
        for gm in GAMEMODES:
            await arcon(rcon_port, f'say @a[gamemode={gm}]')
            await asyncio.sleep(0.05)
            in_gm = read_log(server_path)
            for fp in fake_players:
                if fp in in_gm and fp not in fake_player_modes:
                    fake_player_modes[fp] = gm

        fake_player_reload_commands = []
        for fp in fake_players:
            coords_raw = await arcon(rcon_port, f'execute at {fp} run tp {fp} ~ ~ ~')
            rot0_raw   = await arcon(rcon_port, f'execute as {fp} run data get entity {fp} Rotation[0] 1')
            rot1_raw   = await arcon(rcon_port, f'execute as {fp} run data get entity {fp} Rotation[1] 1')

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

        await ctx.send(embed=discord.Embed(
            description=f'Restarting `{server}` and respawning {len(fake_player_reload_commands)} bot(s) once it is back up...'
        ).set_footer(text='Chronos™'))

        for rp in real_players:
            await arcon(rcon_port, f'kick {rp} SERVER RESTARTING')
        await asyncio.sleep(2.5)
        await stop_server(rcon_port)

        # wait the configured time, then up to as long again for RCON to answer;
        # respawning into a server that isn't up yet silently did nothing
        await asyncio.sleep(max_restart_time)

        async def reply(embed):
            # a slash command's reply window is 15 minutes, a restart can outlive it
            try:
                await ctx.send(embed=embed)
            except discord.HTTPException:
                await ctx.channel.send(embed=embed)

        if not await wait_until_up(rcon_port, max_restart_time):
            print(f'restart {server}: not back up, bot spawn commands:')
            for command in fake_player_reload_commands:
                print('  ' + command)
            await reply(discord.Embed(
                description=f'`{server}` was not back up {2 * max_restart_time:.0f} s after the stop, '
                            f'so the {len(fake_player_reload_commands)} bot(s) were **not** respawned. '
                            'Their spawn commands are in the bot console.'
            ).set_footer(text='Chronos™'))
            return
        for command in fake_player_reload_commands:
            await arcon(rcon_port, command)
        await reply(discord.Embed(
            description=f'`{server}` is back up, {len(fake_player_reload_commands)} bot(s) respawned.'
        ).set_footer(text='Chronos™'))


async def setup(bot):
    await bot.add_cog(restart(bot))
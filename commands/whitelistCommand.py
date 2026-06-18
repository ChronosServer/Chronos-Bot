import discord
from discord.ext import commands
import json
import socket
from rcon import Client
from socket import error as socket_error
import errno

f = open('config.json')
data = json.load(f)
rcon_pass = data['server']['rcon_pass']
server_cfg = data['server']

RCON_PORTS = {
    k.replace('_rcon_port', ''): v
    for k, v in server_cfg.items()
    if k.endswith('_rcon_port')
}
f.close()


def rcon_whitelist(rcon_port, action, player):
    try:
        with Client('127.0.0.1', int(rcon_port), passwd=rcon_pass, timeout=1.5) as client:
            return client.run(f'whitelist {action} {player}')
    except socket.timeout:
        return 'timeout'
    except socket_error as e:
        if e.errno == errno.ECONNREFUSED:
            return 'refused'
        raise


def rcon_whitelist_list(rcon_port):
    try:
        with Client('127.0.0.1', int(rcon_port), passwd=rcon_pass, timeout=1.5) as client:
            return client.run('whitelist list')
    except socket.timeout:
        return 'timeout'
    except socket_error as e:
        if e.errno == errno.ECONNREFUSED:
            return 'refused'
        raise


def parse_whitelist_list(response):
    if response == 'timeout':
        return None, 'Timed out'
    if response == 'refused':
        return None, 'Server offline or RCON unavailable'
    if response is None:
        return None, 'No response'
    if 'no whitelisted players' in response.lower():
        return [], None
    if ':' in response:
        names_part = response.split(':', 1)[1]
        names = [n.strip() for n in names_part.split(',') if n.strip()]
        return names, None
    return None, f'Unrecognized response: {response}'


def get_status(response):
    if response == 'timeout':
        return '⏱'
    if response == 'refused':
        return '✗'
    if response and 'already' in response.lower():
        return '↩'
    if response and ('added' in response.lower() or 'removed' in response.lower()):
        return '✓'
    return '✗'


def whitelist_players(players, servers, action):
    return {
        player: {server: rcon_whitelist(port, action, player) for server, port in servers.items()}
        for player in players
    }


def build_confirmation_embeds(results, action):
    lines = [
        f'**{player}** — ' + ', '.join(f'{s}: {get_status(r)}' for s, r in server_results.items())
        for player, server_results in results.items()
    ]

    chunks = []
    current = []
    current_len = 0
    for line in lines:
        if current_len + len(line) + 1 > 4000:
            chunks.append(current)
            current = []
            current_len = 0
        current.append(line)
        current_len += len(line) + 1
    if current:
        chunks.append(current)

    title = 'Whitelist Add Results' if action == 'add' else 'Whitelist Remove Results'
    embeds = []
    for idx, chunk in enumerate(chunks):
        em = discord.Embed(
            title=title if idx == 0 else None,
            description='\n'.join(chunk)
        )
        em.set_footer(text='✓ done  ↩ already/not whitelisted  ⏱ timeout  ✗ failed  •  Chronos™')
        embeds.append(em)
    return embeds


def build_list_embeds(server_results):
    chunks = []
    current = []
    current_len = 0

    for server, (names, error) in server_results.items():
        if error:
            entries = [f'**[{server.upper()}]** {error}']
        elif not names:
            entries = [f'**[{server.upper()}]** (0)']
        else:
            entries = [f'**[{server.upper()}]** ({len(names)}):'] + [f'`{n}`' for n in names]

        for line in entries:
            if current_len + len(line) + 1 > 4000:
                chunks.append(current)
                current = []
                current_len = 0
            current.append(line)
            current_len += len(line) + 1

    if current:
        chunks.append(current)

    embeds = []
    for idx, chunk in enumerate(chunks):
        em = discord.Embed(
            title='Whitelist' if idx == 0 else None,
            description='\n'.join(chunk)
        )
        em.set_footer(text='Chronos™')
        embeds.append(em)
    return embeds


class whitelist(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(help='Whitelist players. Usage: `!!whitelist <add/remove> <server/all> <player>`, `!!whitelist list <server>`, or attach a `.txt` file with add/remove (Admin Only)')
    @commands.has_permissions(administrator=True)
    async def whitelist(self, ctx, action, server, *, player=None):
        if action == 'list':
            if server not in RCON_PORTS:
                await ctx.send(embed=discord.Embed(
                    description=f'Unknown server. Valid: {", ".join(RCON_PORTS)}'
                ).set_footer(text='Chronos™'))
                return
            names, error = parse_whitelist_list(rcon_whitelist_list(RCON_PORTS[server]))
            for em in build_list_embeds({server: (names, error)}):
                await ctx.send(embed=em)
            return

        if action not in ('add', 'remove'):
            await ctx.send(embed=discord.Embed(
                description=f'Unknown action `{action}`. Valid: `add`, `remove`, `list`'
            ).set_footer(text='Chronos™'))
            return

        if server == 'all':
            servers = RCON_PORTS
        elif server in RCON_PORTS:
            servers = {server: RCON_PORTS[server]}
        else:
            await ctx.send(embed=discord.Embed(
                description=f'Unknown server `{server}`. Valid: `all`, {", ".join(RCON_PORTS)}'
            ).set_footer(text='Chronos™'))
            return

        if ctx.message.attachments:
            attachment = ctx.message.attachments[0]
            if not attachment.filename.endswith('.txt'):
                await ctx.send(embed=discord.Embed(description='Attachment must be a `.txt` file.').set_footer(text='Chronos™'))
                return
            content = await attachment.read()
            players = [line.strip() for line in content.decode('utf-8').splitlines() if line.strip()]
        elif player:
            players = [player]
        else:
            await ctx.send(embed=discord.Embed(description='Provide a player name or attach a `.txt` file.').set_footer(text='Chronos™'))
            return

        if not players:
            await ctx.send(embed=discord.Embed(description='No players found.').set_footer(text='Chronos™'))
            return

        results = whitelist_players(players, servers, action)
        for em in build_confirmation_embeds(results, action):
            await ctx.send(embed=em)


async def setup(bot):
    await bot.add_cog(whitelist(bot))
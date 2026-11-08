import asyncio
import discord
from discord import app_commands
from discord.ext import commands
import json
import os
import sys
from typing import Literal
from nbt import nbt

sys.path.append('./Chronos-Library/')
from defaultRconLibrary import defaultRcon

f = open('config.json')
data = json.load(f)
smp_path = data['server']['smp_path']
smp_world_name = data['server']['smp_world_name']
minecraft_version = data['server']['minecraft_version']
member_role = int(data['bot']['member_role'])
trial_member_role = int(data['bot']['trial_member_role'])
inactive_member_role = int(data['bot']['inactive_member_role'])
smp_rcon_port = data['server']['smp_rcon_port']
rcon_pass = data['server']['rcon_pass']
f.close()

with open(f'dictionary-{minecraft_version}.json') as f:
    SCOREBOARD_DICT = json.load(f)

SCOREBOARD_PATH = os.path.join(smp_path, smp_world_name, 'data', 'minecraft', 'scoreboard.dat')
STATS_PATH = os.path.join(smp_path, smp_world_name, 'players', 'stats')
USERCACHE_PATH = os.path.join(smp_path, 'usercache.json')

CATEGORIES = Literal['killed', 'killed_by', 'dropped', 'picked_up', 'used', 'mined', 'broken', 'crafted', 'custom', 'dig', 'scoreboard']

SETDISPLAY_ROLES = {member_role, trial_member_role, inactive_member_role}

# scoreboards are named <prefix>-<name>, the prefix says which stat category they track
CATEGORY_PREFIX = {'mined': 'm', 'used': 'u', 'crafted': 'c', 'broken': 'b', 'dropped': 'd', 'picked_up': 'p', 'killed': 'k', 'killed_by': 'kb', 'custom': 'z'}
DIG_TARGETS = ['all', 'axe', 'pickaxe', 'shears', 'shovel']

# full scoreboard names for autocomplete (the dictionary keys), plus the dig-* scoreboards
SCOREBOARD_NAMES = list(SCOREBOARD_DICT) + [f'dig-{t}' for t in DIG_TARGETS]
TARGETS_BY_CATEGORY = {
    category: [key.split('-', 1)[1] for key in SCOREBOARD_DICT if key.split('-', 1)[0] == prefix]
    for category, prefix in CATEGORY_PREFIX.items()
}


def load_usercache():
    with open(USERCACHE_PATH) as f:
        cache = json.load(f)
    return {entry['uuid']: entry['name'] for entry in cache}


def get_team_players(scoreboards):
    members = set()
    for team in scoreboards['Teams']:
        if 'Players' not in team:
            continue
        for player in team['Players']:
            members.add(player.value)
    return members


def autocomplete_choices(options, current, limit=25):
    current = current.lower()
    matches = [o for o in options if o.startswith(current)]
    if len(matches) < limit:
        matches += [o for o in options if current in o and not o.startswith(current)]
    return [app_commands.Choice(name=o, value=o) for o in matches[:limit]]


# The three functions below read big files, so the commands run them in a thread.
# They return (title, entries, error): error is a message to show instead of a ranking.

def scoreboard_entries(name, include_bots):
    scoreboards = nbt.NBTFile(SCOREBOARD_PATH)['data']
    objectives = {s['Name'].value for s in scoreboards['Objectives']}
    resolved = SCOREBOARD_DICT.get(name, name)

    if resolved not in objectives:
        return None, None, f'Unknown scoreboard `{name}`.'

    team_players = None if include_bots else get_team_players(scoreboards)
    entries = [
        (entry['Name'].value, entry['Score'].value)
        for entry in scoreboards['PlayerScores']
        if entry['Objective'].value == resolved
        and 'Score' in entry
        and entry['Score'].value != 0
        and (team_players is None or entry['Name'].value in team_players)
    ]

    if not entries:
        return None, None, f'No scores found for `{name}`.'

    entries.sort(key=lambda x: x[1], reverse=True)
    return f'{name} — Rankings', entries, None


def player_stat_entries(category, name, include_bots):
    stat_key = f'minecraft:{name}' if ':' not in name else name
    mc_category = f'minecraft:{category}'
    team_players = None if include_bots else get_team_players(nbt.NBTFile(SCOREBOARD_PATH)['data'])
    uuid_to_name = load_usercache()

    entries = []
    for uuid, player_name in uuid_to_name.items():
        if team_players is not None and player_name not in team_players:
            continue
        stat_file = os.path.join(STATS_PATH, f'{uuid}.json')
        if not os.path.exists(stat_file):
            continue
        with open(stat_file) as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                continue
        value = data.get('stats', {}).get(mc_category, {}).get(stat_key, 0)
        if value != 0:
            entries.append((player_name, value))

    if not entries:
        return None, None, f'No data found for `{mc_category}.{stat_key}`.'

    entries.sort(key=lambda x: x[1], reverse=True)
    return f'minecraft:{category}.{stat_key} — Rankings', entries, None


def resolve_objective(name):
    # returns the real objective for a full or truncated name, or None if it doesn't exist
    scoreboards = nbt.NBTFile(SCOREBOARD_PATH)['data']
    objectives = {s['Name'].value for s in scoreboards['Objectives']}
    resolved = SCOREBOARD_DICT.get(name, name)
    return resolved if resolved in objectives else None


def build_embeds(title, entries, total, threshold=1000):
    footer = f'Total: {total:,}  |  {total / 1_000_000:.2f}M  •  Chronos™'

    def chunk_to_fit(players, scores, limit=1024):
        chunks = []
        i = 0
        while i < len(players):
            j = i
            while j < len(players):
                if (len('\n'.join(players[i:j+1])) <= limit and
                        len('\n'.join(scores[i:j+1])) <= limit):
                    j += 1
                else:
                    break
            if j == i:
                j = i + 1
            chunks.append((players[i:j], scores[i:j]))
            i = j
        return chunks

    all_players = [f'`{i+1}. {name}`' for i, (name, _) in enumerate(entries)]
    all_scores = [f'`{score:,}`' for _, score in entries]
    chunks = chunk_to_fit(all_players, all_scores)

    filtered_chunks = []
    for idx, (players, scores) in enumerate(chunks):
        if idx > 0:
            paired = [(p, s) for p, s in zip(players, scores) if int(s.replace(',', '').replace('`', '')) >= threshold]
            if not paired:
                continue
            players, scores = zip(*paired)
        filtered_chunks.append((players, scores))

    embeds = []
    for idx, (players, scores) in enumerate(filtered_chunks):
        is_last = idx == len(filtered_chunks) - 1
        em = discord.Embed(title=title if idx == 0 else None)
        em.add_field(name='Player', value='\n'.join(players), inline=True)
        em.add_field(name='Score', value='\n'.join(scores), inline=True)
        em.set_footer(text=footer if is_last else 'Chronos™')
        embeds.append(em)

    return embeds


class stats(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(
        name='stats',
        description='Leaderboards from player stats and scoreboards',
        help=(
            '`!!stats <category> <target> [include_bots]`, set `include_bots` to `true` to include bots. '
            '`<category>`: `killed`, `killed_by`, `dropped`, `picked_up`, `used`, `mined`, `broken`, `crafted`, `custom`, `dig`, `scoreboard`, '
            'Targets: `killed`, `killed_by` → entity type, `picked_up`, `used`, `mined`, `broken`, `crafted` → block/item id, '
            '`dig` → `all`, `axe`, `pickaxe`, `shears`, `shovel`, `scoreboard` → scoreboard name, '
            'For `custom` or more info: https://minecraft.gamepedia.com/Statistics, '
            'Examples: `!!stats used diamond_pickaxe` `!!stats custom time_since_rest true`'
        )
    )
    @app_commands.describe(
        category='Stat category (dig = digging scoreboards, scoreboard = any scoreboard by name)',
        target='Entity/block/item id, or scoreboard name; for dig: all, axe, pickaxe, shears, shovel',
        include_bots='Include bots (fake players) in the ranking'
    )
    async def stats(self, ctx, category: CATEGORIES, target: str, include_bots: bool = False):
        await ctx.defer()

        if category == 'scoreboard':
            title, entries, error = await asyncio.to_thread(scoreboard_entries, target, include_bots)
        elif category == 'dig':
            scoreboard_name = target if target.startswith('dig-') else f'dig-{target}'
            title, entries, error = await asyncio.to_thread(scoreboard_entries, scoreboard_name, include_bots)
        else:
            title, entries, error = await asyncio.to_thread(player_stat_entries, category, target, include_bots)

        if error:
            await ctx.send(embed=discord.Embed(description=error).set_footer(text='Chronos™'))
            return

        total = sum(s for _, s in entries)
        for em in build_embeds(title, entries, total):
            await ctx.send(embed=em)

    @stats.autocomplete('target')
    async def stats_target_autocomplete(self, interaction: discord.Interaction, current: str):
        category = getattr(interaction.namespace, 'category', None)
        if category == 'scoreboard':
            options = SCOREBOARD_NAMES
        elif category == 'dig':
            options = DIG_TARGETS
        else:
            options = TARGETS_BY_CATEGORY.get(category, [])
        return autocomplete_choices(options, current)

    @commands.hybrid_command(
        name='setdisplay',
        description='Show a scoreboard in the SMP sidebar (members only)',
        help='Set the SMP sidebar display, Usage: `!!setdisplay <scoreboard>` (accepts full or truncated names) (Member/Trial/Inactive Only)'
    )
    @app_commands.describe(scoreboard='Scoreboard name, full or truncated')
    @commands.guild_only()
    async def setdisplay(self, ctx, scoreboard: str):
        await ctx.defer()
        author_role_ids = {r.id for r in ctx.author.roles}
        if not author_role_ids & SETDISPLAY_ROLES:
            await ctx.send(embed=discord.Embed(description='Insufficient permission').set_footer(text='Chronos™'))
            return

        resolved = await asyncio.to_thread(resolve_objective, scoreboard)
        if resolved is None:
            await ctx.send(embed=discord.Embed(description=f'Unknown scoreboard `{scoreboard}`.').set_footer(text='Chronos™'))
            return

        response = await defaultRcon(smp_rcon_port, rcon_pass, f'scoreboard objectives setdisplay sidebar {resolved}')

        if response is None:
            await ctx.send(embed=discord.Embed(description=f'Sidebar display set to `{resolved}`.').set_footer(text='Chronos™'))
        else:
            await ctx.send(embed=discord.Embed(description=response).set_footer(text='Chronos™'))

    @setdisplay.autocomplete('scoreboard')
    async def setdisplay_scoreboard_autocomplete(self, interaction: discord.Interaction, current: str):
        return autocomplete_choices(SCOREBOARD_NAMES, current)


async def setup(bot):
    await bot.add_cog(stats(bot))
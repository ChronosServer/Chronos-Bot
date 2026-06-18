import discord
from discord.ext import commands
import json
import os
import sys
from nbt import nbt

sys.path.append('./Chronos-Library/')
from defaultRconLibrary import defaultRcon

f = open('config.json')
data = json.load(f)
smp_path = data['server']['smp_path']
smp_world_name = data['server']['smp_world_name']
member_role = int(data['bot']['member_role'])
trial_member_role = int(data['bot']['trial_member_role'])
inactive_member_role = int(data['bot']['inactive_member_role'])
smp_rcon_port = data['server']['smp_rcon_port']
rcon_pass = data['server']['rcon_pass']
f.close()

with open('dictionary-26.2.json') as f:
    SCOREBOARD_DICT = json.load(f)

SCOREBOARD_PATH = os.path.join(smp_path, smp_world_name, 'data', 'minecraft', 'scoreboard.dat')
STATS_PATH = os.path.join(smp_path, smp_world_name, 'players', 'stats')
USERCACHE_PATH = os.path.join(smp_path, 'usercache.json')

STAT_CATEGORIES = {'killed', 'killed_by', 'dropped', 'picked_up', 'used', 'mined', 'broken', 'crafted', 'custom'}

SETDISPLAY_ROLES = {member_role, trial_member_role, inactive_member_role}


def load_usercache():
    with open(USERCACHE_PATH) as f:
        cache = json.load(f)
    return {entry['uuid']: entry['name'] for entry in cache}


def get_team_players():
    scoreboards = nbt.NBTFile(SCOREBOARD_PATH)['data']
    members = set()
    for team in scoreboards['Teams']:
        if 'Players' not in team:
            continue
        for player in team['Players']:
            members.add(player.value)
    return members


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

    @commands.command(
        help=(
            '`!!stats <classification> <target> [-bot]`, Add `-bot` to include bots. `<classification>`: `killed`, `killed_by`, `dropped`, `picked_up`, `used`, `mined`, `broken`, `crafted`, `custom`, `dig`, Targets: `killed`, `killed_by` → entity type, `picked_up`, `used`, `mined`, `broken`, `crafted` → block/item id, `dig` → `all`, `axe`, `pickaxe`, `shears`, `shovel` `scoreboard` → scoreboard name, For `custom` or more info: https://minecraft.gamepedia.com/Statistics, Examples: `!!stats used diamond_pickaxe` `!!stats custom time_since_rest -bot`, `!!stats setdisplay <scoreboard>` sets the SMP sidebar display (accepts full or truncated names) (Member/Trial/Inactive Only)'
        )
    )
    async def stats(self, ctx, category, *args):
        if category == 'setdisplay':
            author_role_ids = {r.id for r in ctx.author.roles}
            if not author_role_ids & SETDISPLAY_ROLES:
                await ctx.send(embed=discord.Embed(description='Insufficient permission').set_footer(text='Chronos™'))
                return
            if not args:
                await ctx.send(embed=discord.Embed(description='Missing scoreboard name. Usage: `!!stats setdisplay <scoreboard>`').set_footer(text='Chronos™'))
                return
            await self._setdisplay(ctx, ' '.join(args))
            return

        if not args:
            await ctx.send(embed=discord.Embed(description='Missing target. Usage: `!!stats <category> <target> [-bot]`').set_footer(text='Chronos™'))
            return

        include_bots = args[-1] == '-bot'
        name = ' '.join(args[:-1] if include_bots else args)

        if not name:
            await ctx.send(embed=discord.Embed(description='Missing target.').set_footer(text='Chronos™'))
            return

        team_players = None if include_bots else get_team_players()

        if category == 'scoreboard':
            await self._scoreboard(ctx, name, team_players)
        elif category == 'dig':
            scoreboard_name = name if name.startswith('dig-') else f'dig-{name}'
            await self._scoreboard(ctx, scoreboard_name, team_players)
        elif category in STAT_CATEGORIES:
            await self._player_stats(ctx, category, name, team_players)
        else:
            await ctx.send(embed=discord.Embed(
                description=f'Unknown category `{category}`. Use: `{", ".join(sorted(STAT_CATEGORIES))}`, `scoreboard`, `dig`, or `setdisplay`.'
            ).set_footer(text='Chronos™'))

    async def _scoreboard(self, ctx, name, team_players):
        scoreboards = nbt.NBTFile(SCOREBOARD_PATH)['data']
        objectives = [s['Name'].value for s in scoreboards['Objectives']]

        if name not in objectives:
            await ctx.send(embed=discord.Embed(description=f'Unknown scoreboard `{name}`.').set_footer(text='Chronos™'))
            return

        entries = [
            (entry['Name'].value, entry['Score'].value)
            for entry in scoreboards['PlayerScores']
            if entry['Objective'].value == name
            and 'Score' in entry
            and entry['Score'].value != 0
            and (team_players is None or entry['Name'].value in team_players)
        ]

        if not entries:
            await ctx.send(embed=discord.Embed(description=f'No scores found for `{name}`.').set_footer(text='Chronos™'))
            return

        entries.sort(key=lambda x: x[1], reverse=True)
        total = sum(s for _, s in entries)
        for em in build_embeds(f'{name} — Rankings', entries, total):
            await ctx.send(embed=em)

    async def _player_stats(self, ctx, category, name, team_players):
        stat_key = f'minecraft:{name}' if ':' not in name else name
        mc_category = f'minecraft:{category}'
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
            await ctx.send(embed=discord.Embed(description=f'No data found for `{mc_category}.{stat_key}`.').set_footer(text='Chronos™'))
            return

        entries.sort(key=lambda x: x[1], reverse=True)
        total = sum(s for _, s in entries)
        for em in build_embeds(f'minecraft:{category}.{stat_key} — Rankings', entries, total):
            await ctx.send(embed=em)

    async def _setdisplay(self, ctx, name):
        scoreboards = nbt.NBTFile(SCOREBOARD_PATH)['data']
        objectives = {s['Name'].value for s in scoreboards['Objectives']}

        resolved = SCOREBOARD_DICT.get(name, name)

        if resolved not in objectives:
            await ctx.send(embed=discord.Embed(description=f'Unknown scoreboard `{name}`.').set_footer(text='Chronos™'))
            return

        response = await defaultRcon(smp_rcon_port, rcon_pass, f'scoreboard objectives setdisplay sidebar {resolved}')

        if response is None:
            await ctx.send(embed=discord.Embed(description=f'Sidebar display set to `{resolved}`.').set_footer(text='Chronos™'))
        else:
            await ctx.send(embed=discord.Embed(description=response).set_footer(text='Chronos™'))


async def setup(bot):
    await bot.add_cog(stats(bot))
import discord
from discord import app_commands
from discord.ext import commands
import json
import os
from typing import Literal

f = open('config.json')
data = json.load(f)
member_role = int(data['bot']['member_role'])
trial_member_role = int(data['bot']['trial_member_role'])
inactive_member_role = int(data['bot']['inactive_member_role'])
server_cfg = data['server']

MEMBER_ROLES = {member_role, trial_member_role, inactive_member_role}

SERVER_PATHS = {
    k.replace('_path', ''): server_cfg[k].rstrip('/') + '/' + server_cfg[k.replace('_path', '_world_name')]
    for k in server_cfg
    if k.endswith('_path') and k.replace('_path', '_world_name') in server_cfg
}

DIMENSION_PATHS = {
    'ow':     ('dimensions/minecraft/overworld/region',       'dimensions/minecraft/overworld/entities'),
    'nether': ('dimensions/minecraft/the_nether/region', 'dimensions/minecraft/the_nether/entities'),
    'end':    ('dimensions/minecraft/the_end/region',  'dimensions/minecraft/the_end/entities'),
}
f.close()

SERVERS = Literal[tuple(SERVER_PATHS)]


class region(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(
        name='region',
        description='Download a region file and its entities file from a server (members only)',
        help='Get region files from servers, Usage: `!!region <server> <ow/nether/end> <region (e.g. r.0.0)>` (Member Only)'
    )
    @app_commands.describe(
        server='Server to get the region files from',
        dimension='Dimension: ow = overworld, nether, end',
        region='Region name as in the world folder, e.g. r.0.0 or r.-3.2'
    )
    @commands.guild_only()
    async def region(self, ctx, server: SERVERS, dimension: Literal['ow', 'nether', 'end'], region: str):
        await ctx.defer()
        author_role_ids = {r.id for r in ctx.author.roles}
        if not author_role_ids & MEMBER_ROLES:
            await ctx.send(embed=discord.Embed(description='Insufficient permission').set_footer(text='Chronos™'))
            return

        if os.path.basename(region) != region:
            await ctx.send(embed=discord.Embed(description='Invalid region name.').set_footer(text='Chronos™'))
            return

        world_path = SERVER_PATHS[server]
        dim = DIMENSION_PATHS[dimension]

        region_path = os.path.join(world_path, dim[0], f'{region}.mca')
        entities_path = os.path.join(world_path, dim[1], f'{region}.mca')

        region_exists = os.path.exists(region_path)
        entities_exists = os.path.exists(entities_path)

        if not region_exists and not entities_exists:
            await ctx.send(embed=discord.Embed(description=f'Neither region nor entities file found for `{region}`.').set_footer(text='Chronos™'))
            return

        notes = []
        if not region_exists:
            notes.append('⚠️ Region file not found')
        if not entities_exists:
            notes.append('⚠️ Entities file not found')

        description = '\n'.join(notes) if notes else None
        embed = discord.Embed(title=f'{server.upper()} {dimension.upper()} region {region}', description=description)
        embed.set_footer(text='Chronos™')
        await ctx.send(embed=embed)

        # Discord refuses files over the server's upload limit (10 MB without
        # boosts); a big region file would otherwise fail with no reply
        limit = ctx.guild.filesize_limit
        for label, path, exists in (('Region', region_path, region_exists),
                                    ('Entities', entities_path, entities_exists)):
            if not exists:
                continue
            size = os.path.getsize(path)
            if size > limit:
                await ctx.send(embed=discord.Embed(
                    description=f'⚠️ {label} file is {size / 1048576:.1f} MB, over the '
                                f'{limit / 1048576:.0f} MB upload limit of this server, so it can\'t be sent.'
                ).set_footer(text='Chronos™'))
                continue
            await ctx.send(file=discord.File(path))


async def setup(bot):
    await bot.add_cog(region(bot))
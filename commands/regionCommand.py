import discord
from discord.ext import commands
import json
import os

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


class region(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(help='Get region files from servers, Usage: `!!region <server> <ow/nether/end> <region (e.g. r.0.0)>` (Member Only)')
    async def region(self, ctx, server, dimension, region):
        author_role_ids = {r.id for r in ctx.author.roles}
        if not author_role_ids & MEMBER_ROLES:
            await ctx.send(embed=discord.Embed(description='Insufficient permission').set_footer(text='Chronos™'))
            return

        world_path = SERVER_PATHS.get(server)
        if world_path is None:
            await ctx.send(embed=discord.Embed(description=f"Unknown server `{server}`. Valid: {', '.join(SERVER_PATHS)}").set_footer(text='Chronos™'))
            return

        dim = DIMENSION_PATHS.get(dimension)
        if dim is None:
            await ctx.send(embed=discord.Embed(description=f"Unknown dimension `{dimension}`. Valid: ow, nether, end").set_footer(text='Chronos™'))
            return

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

        if region_exists:
            await ctx.send(file=discord.File(region_path))
        if entities_exists:
            await ctx.send(file=discord.File(entities_path))


async def setup(bot):
    await bot.add_cog(region(bot))
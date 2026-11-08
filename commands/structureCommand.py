import discord
from discord import app_commands
from discord.ext import commands
import json
import os
import sys
from typing import Literal
sys.path.append('./Chronos-Library/')
from filesizeLibrary import get_size_format, get_file_size

f = open('config.json')
data = json.load(f)
member_role = int(data['bot']['member_role'])
trial_member_role = int(data['bot']['trial_member_role'])
inactive_member_role = int(data['bot']['inactive_member_role'])
server_cfg = data['server']

MEMBER_ROLES = {member_role, trial_member_role, inactive_member_role}

# build {server: structure_path} for all non-smp servers that have both _path and _world_name
STRUCTURE_PATHS = {
    k.replace('_path', ''): os.path.join(server_cfg[k], server_cfg[k.replace('_path', '_world_name')], 'generated', 'minecraft', 'structure')
    for k in server_cfg
    if k.endswith('_path')
    and k.replace('_path', '_world_name') in server_cfg
    and not k.startswith('smp')
}
f.close()

SERVERS = Literal[tuple(STRUCTURE_PATHS)]


class structure(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(
        name='structure',
        description='Download, upload or list structure (.nbt) files on a server (members only)',
        help='Download/upload/list structure files, Usage: `!!structure <server> <download/upload/list> <name (download only, without .nbt)>`, attach the .nbt file when uploading (Member Only)'
    )
    @app_commands.describe(
        server='Server whose structure folder to use',
        action='download = get a structure, upload = add the attached .nbt, list = show all structures',
        name='Structure name without .nbt (download only)',
        file='The .nbt structure file to upload (upload only)'
    )
    @commands.guild_only()
    async def structure(self, ctx, server: SERVERS, action: Literal['download', 'upload', 'list'], name: str = None, file: discord.Attachment = None):
        await ctx.defer()
        author_role_ids = {r.id for r in ctx.author.roles}
        if not author_role_ids & MEMBER_ROLES:
            await ctx.send(embed=discord.Embed(description='Insufficient permission').set_footer(text='Chronos™'))
            return

        structure_path = STRUCTURE_PATHS[server]

        if action == 'upload':
            if file is None:
                await ctx.send(embed=discord.Embed(description='Attach the `.nbt` structure file you want to upload.').set_footer(text='Chronos™'))
                return
            filename = file.filename
            if not filename.endswith('.nbt'):
                await ctx.send(embed=discord.Embed(description='Only `.nbt` files can be uploaded.').set_footer(text='Chronos™'))
                return
            await file.save(fp=os.path.join(structure_path, filename))
            embed = discord.Embed(title=f'{filename} has successfully been uploaded to {server.upper()}')
            embed.add_field(name='Structure filesize is', value=get_size_format(file.size), inline=False)
            embed.set_footer(text='Chronos™')
            await ctx.send(embed=embed)

        elif action == 'download':
            if name is None:
                await ctx.send(embed=discord.Embed(description='Please provide a structure name.').set_footer(text='Chronos™'))
                return
            if os.path.basename(name) != name:
                await ctx.send(embed=discord.Embed(description='Invalid structure name.').set_footer(text='Chronos™'))
                return
            filepath = os.path.join(structure_path, f'{name}.nbt')
            if not os.path.isfile(filepath):
                await ctx.send(embed=discord.Embed(description=f'Structure `{name}` was not found on {server.upper()}.').set_footer(text='Chronos™'))
                return
            embed = discord.Embed(title=f'Structure {name}', description=f'Filesize is {get_size_format(get_file_size(filepath))}')
            embed.set_footer(text='Chronos™')
            await ctx.send(embed=embed)
            await ctx.send(file=discord.File(filepath))

        elif action == 'list':
            strucfiles = os.listdir(structure_path)
            if not strucfiles:
                await ctx.send(embed=discord.Embed(description='No structure files found.').set_footer(text='Chronos™'))
                return

            lines_name = []
            lines_size = []
            for item in strucfiles:
                lines_name.append(item)
                lines_size.append(get_size_format(get_file_size(os.path.join(structure_path, item))))

            # split into chunks that fit discord's 1024 char field limit
            chunks = []
            i = 0
            while i < len(lines_name):
                j = i
                while j < len(lines_name):
                    if (len('\n'.join(lines_name[i:j+1])) <= 1024 and
                            len('\n'.join(lines_size[i:j+1])) <= 1024):
                        j += 1
                    else:
                        break
                if j == i:
                    j = i + 1
                chunks.append((lines_name[i:j], lines_size[i:j]))
                i = j

            for idx, (names, sizes) in enumerate(chunks):
                em = discord.Embed(title=f'Structure files on {server.upper()}' if idx == 0 else None)
                em.add_field(name='File', value='\n'.join(names), inline=True)
                em.add_field(name='Size', value='\n'.join(sizes), inline=True)
                em.set_footer(text='Chronos™')
                await ctx.send(embed=em)

    @structure.autocomplete('name')
    async def structure_name_autocomplete(self, interaction: discord.Interaction, current: str):
        if not {r.id for r in getattr(interaction.user, 'roles', [])} & MEMBER_ROLES:
            return []
        path = STRUCTURE_PATHS.get(getattr(interaction.namespace, 'server', None))
        if path is None or not os.path.isdir(path):
            return []
        names = sorted(n[:-4] for n in os.listdir(path) if n.endswith('.nbt'))
        return [app_commands.Choice(name=n, value=n) for n in names if current.lower() in n.lower() and len(n) <= 100][:25]


async def setup(bot):
    await bot.add_cog(structure(bot))
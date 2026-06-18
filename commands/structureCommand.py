import discord
from discord.ext import commands
import json
import os
import sys
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


class structure(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(help='Download/upload/list structure files, Usage: `!!structure <server> <download/upload/list> <name (download only, without .nbt)>` (Member Only)')
    async def structure(self, ctx, server, strucaction, strucname=None):
        author_role_ids = {r.id for r in ctx.author.roles}
        if not author_role_ids & MEMBER_ROLES:
            await ctx.send(embed=discord.Embed(description='Insufficient permission').set_footer(text='Chronos™'))
            return

        structure_path = STRUCTURE_PATHS.get(server)
        if structure_path is None:
            await ctx.send(embed=discord.Embed(description=f"Unknown server `{server}`. Valid: {', '.join(STRUCTURE_PATHS)}").set_footer(text='Chronos™'))
            return

        if strucaction == 'upload':
            if not ctx.message.attachments:
                return
            attachment = ctx.message.attachments[0]
            filename = attachment.filename
            if not filename.endswith('.nbt'):
                return
            await attachment.save(fp=os.path.join(structure_path, filename))
            embed = discord.Embed(title=f'{filename} has successfully been uploaded to {server.upper()}')
            embed.add_field(name='Structure filesize is', value=get_size_format(attachment.size), inline=False)
            embed.set_footer(text='Chronos™')
            await ctx.send(embed=embed)

        elif strucaction == 'download':
            if strucname is None:
                await ctx.send(embed=discord.Embed(description='Please provide a structure name.').set_footer(text='Chronos™'))
                return
            filepath = os.path.join(structure_path, f'{strucname}.nbt')
            embed = discord.Embed(title=f'Structure {strucname}', description=f'Filesize is {get_size_format(get_file_size(filepath))}')
            embed.set_footer(text='Chronos™')
            await ctx.send(embed=embed)
            await ctx.send(file=discord.File(filepath))

        elif strucaction == 'list':
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


async def setup(bot):
    await bot.add_cog(structure(bot))
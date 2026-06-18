# commands/membersExport.py
import discord
from discord.ext import commands, tasks
import json
import os

DATA_DIR = '/var/www/html/data'
GUILD_ID = 861217564130279425
ROLE_MEMBER = 890473812917891092
ROLE_TRIAL = 864953821741580319
ROLE_INACTIVE = 891094488385200148

os.makedirs(DATA_DIR, exist_ok=True)


class membersExport(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.export_members.start()

    def cog_unload(self):
        self.export_members.cancel()

    @tasks.loop(hours=8)
    async def export_members(self):
        await self.bot.wait_until_ready()
        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
            print("Members export: guild not found")
            return

        await guild.chunk()

        members = []
        trial = []
        inactive = []

        role_ids = {m.id for m in guild.roles}

        for member in guild.members:
            member_role_ids = {r.id for r in member.roles}

            entry = {
                'name': member.display_name,
                'avatar': str(member.display_avatar.url),
            }

            if ROLE_INACTIVE in member_role_ids:
                inactive.append(entry)
            elif ROLE_MEMBER in member_role_ids:
                members.append(entry)
            elif ROLE_TRIAL in member_role_ids:
                trial.append(entry)

        output = {
            'members': sorted(members, key=lambda x: x['name'].lower()),
            'trial': sorted(trial, key=lambda x: x['name'].lower()),
            'inactive': sorted(inactive, key=lambda x: x['name'].lower()),
        }

        with open(os.path.join(DATA_DIR, 'members.json'), 'w') as f:
            json.dump(output, f)

        print(f"Members export: {len(members)} members, {len(trial)} trial, {len(inactive)} inactive")

    @export_members.before_loop
    async def before_export(self):
        await self.bot.wait_until_ready()

    @commands.command(name='sync_members')
    @commands.has_permissions(administrator=True)
    async def sync_members(self, ctx):
        await ctx.send('⏳ Syncing members...')
        try:
            await self.export_members()
            await ctx.send('✅ Members synced.')
        except Exception as e:
            await ctx.send(f'❌ Members sync failed: {e}')


async def setup(bot):
    await bot.add_cog(membersExport(bot))
# commands/membersExport.py
import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
import os
import time
import asyncio
import traceback
from datetime import datetime

with open('config.json') as f:
    data = json.load(f)
GUILD_ID = int(data['bot']['guild_id'])
ROLE_MEMBER = int(data['bot']['member_role'])
ROLE_TRIAL = int(data['bot']['trial_member_role'])
ROLE_INACTIVE = int(data['bot']['inactive_member_role'])
DATA_DIR = os.path.join(data['server']['website_path'], 'data')

EXPORT_EVERY_HOURS = 1
EXPORT_TIMEOUT = 180

os.makedirs(DATA_DIR, exist_ok=True)


def log(msg):
    print(f"[members {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def write_json_atomic(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def record(bot, name, ok, seconds, err=''):
    health = getattr(bot, 'sync_health', None)
    if health is None:
        health = bot.sync_health = {}
    prev = health.get(name, {})
    now = time.time()
    health[name] = {'ok': ok, 'at': now, 'seconds': round(seconds, 1), 'error': err,
                    'last_ok': now if ok else prev.get('last_ok')}


class membersExport(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.lock = asyncio.Lock()
        self.export_loop.start()

    def cog_unload(self):
        self.export_loop.cancel()

    @tasks.loop(hours=EXPORT_EVERY_HOURS)
    async def export_loop(self):
        await self.run_export()

    @export_loop.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    @export_loop.error
    async def _error(self, exc):
        log(f"export_loop crashed: {exc!r}, restarting in 30s")
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        asyncio.get_running_loop().call_later(30, lambda: self.export_loop.is_running() or self.export_loop.start())

    async def run_export(self):
        """Export the member list. Returns None if ok, else an error string. Never raises."""
        if self.lock.locked():
            return 'already running'
        async with self.lock:
            t0 = time.monotonic()
            try:
                counts = await asyncio.wait_for(self._export(), EXPORT_TIMEOUT)
                record(self.bot, 'members', True, time.monotonic() - t0)
                log(counts)
                return None
            except asyncio.TimeoutError:
                err = f"timed out after {EXPORT_TIMEOUT}s"
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                traceback.print_exc()
            record(self.bot, 'members', False, time.monotonic() - t0, err)
            log(f"FAILED, {err}")
            return err

    async def _export(self):
        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
            raise RuntimeError(f"guild {GUILD_ID} not found (wrong guild_id, or the bot is not in it)")
        if not self.bot.intents.members:
            raise RuntimeError("the Members intent is not enabled in the bot code (intents.members = True)")

        try:
            await asyncio.wait_for(guild.chunk(), 90)
        except asyncio.TimeoutError:
            log("guild.chunk() timed out, using the members already cached")

        roles_found = [guild.get_role(r) is not None for r in (ROLE_MEMBER, ROLE_TRIAL, ROLE_INACTIVE)]
        if not any(roles_found):
            raise RuntimeError("none of the member/trial/inactive role ids exist in this guild, check config.json")
        if len(guild.members) < 2:
            raise RuntimeError(f"only {len(guild.members)} member visible, the Members intent is probably not being delivered; keeping the old file")

        members, trial, inactive = [], [], []
        for member in guild.members:
            role_ids = {r.id for r in member.roles}
            # their own Discord name (profile display name, else username),
            # not the nickname they have on the Chronos server
            entry = {'name': member.global_name or member.name,
                     'avatar': str(member.display_avatar.url)}
            if ROLE_INACTIVE in role_ids:
                inactive.append(entry)
            elif ROLE_MEMBER in role_ids:
                members.append(entry)
            elif ROLE_TRIAL in role_ids:
                trial.append(entry)

        output = {
            'members': sorted(members, key=lambda x: x['name'].lower()),
            'trial': sorted(trial, key=lambda x: x['name'].lower()),
            'inactive': sorted(inactive, key=lambda x: x['name'].lower()),
        }
        write_json_atomic(os.path.join(DATA_DIR, 'members.json'), output)
        return f"ok: {len(members)} members, {len(trial)} trial, {len(inactive)} inactive ({len(guild.members)} visible in guild)"

    @commands.hybrid_command(
        name='sync_members',
        description='Re-export the member directory to the website now (admin only)',
        help='Re-export the member directory to the website, Usage: `!!sync_members` (Admin Only)'
    )
    @app_commands.default_permissions(administrator=True)
    @commands.has_permissions(administrator=True)
    async def sync_members(self, ctx):
        await ctx.defer()
        err = await self.run_export()
        await ctx.send('✅ Members synced.' if err is None else f'❌ Members sync failed: {err}')


async def setup(bot):
    await bot.add_cog(membersExport(bot))

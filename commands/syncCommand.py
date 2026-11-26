# commands/syncCommand.py
import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
import os
import asyncio
import time
import traceback
import concurrent.futures
from datetime import datetime, timezone
from typing import Literal
from rcon import Client
import shutil
import threading
from nbt import nbt, region as nbt_region
import requests
import hashlib
import re
import urllib.parse

# load config directly
with open('config.json') as f:
    _cfg = json.load(f)

_bot_cfg = _cfg['bot']
_server_cfg = _cfg['server']

DATA_DIR = os.path.join(_server_cfg['website_path'], 'data')
STATE_FILE = 'sync_state.json'
ARCHIVE_CHANNEL_ID = int(_bot_cfg.get('archive_channel_id') or 0)  # 0 = no forum archive

# how often things refresh, and the most time each step may take before it is abandoned
STATUS_EVERY_SECONDS = 300      # server online/offline list (one RCON ping per server each time)
HEAVY_EVERY_HOURS = 8           # storage scan + forum archive
STEP_TIMEOUT = {'storage': 900, 'archive': 3600, 'status': 60}

os.makedirs(DATA_DIR, exist_ok=True)

MS_REGIONS = _server_cfg['storage_regions']
DIG_REGIONS = _server_cfg['dig_storage_regions']
REGION_SOURCE = os.path.join(_server_cfg['smp_path'], _server_cfg['smp_world_name'], 'dimensions', 'minecraft', 'overworld', 'region')
REGION_CACHE = './region-cache'
os.makedirs(REGION_CACHE, exist_ok=True)

SHULKER_BOXES = {
    'minecraft:white_shulker_box','minecraft:orange_shulker_box','minecraft:magenta_shulker_box',
    'minecraft:light_blue_shulker_box','minecraft:yellow_shulker_box','minecraft:lime_shulker_box',
    'minecraft:pink_shulker_box','minecraft:gray_shulker_box','minecraft:light_gray_shulker_box',
    'minecraft:cyan_shulker_box','minecraft:purple_shulker_box','minecraft:blue_shulker_box',
    'minecraft:brown_shulker_box','minecraft:green_shulker_box','minecraft:red_shulker_box',
    'minecraft:black_shulker_box','minecraft:shulker_box'
}


# ---------- helpers ----------

def log(msg):
    print(f"[sync {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def write_json_atomic(path, obj):
    # write to a temp file and swap it in, so the website never reads a half-written file
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


async def run_step(bot, name, factory, timeout):
    """Run one sync step. Returns None on success or a short error string. Never raises."""
    t0 = time.monotonic()
    try:
        await asyncio.wait_for(factory(), timeout)
        took = time.monotonic() - t0
        record(bot, name, True, took)
        log(f"{name}: ok in {took:.1f}s")
        return None
    except asyncio.TimeoutError:
        err = f"timed out after {timeout}s"
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        traceback.print_exc()
    record(bot, name, False, time.monotonic() - t0, err)
    log(f"{name}: FAILED, {err}")
    return err


# ---------- storage ----------

def _cache_regions(region_names):
    for name in region_names:
        src = os.path.join(REGION_SOURCE, f'{name}.mca')
        dst = os.path.join(REGION_CACHE, f'{name}.mca')
        tmp = dst + '.tmp'
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)


def _count_items(items):
    counts = {}
    for item in items:
        try:
            item_id = item['id'].value
            count = item['count'].value if 'count' in item else 1
            if item_id != 'minecraft:air':
                counts[item_id] = counts.get(item_id, 0) + count
            if item_id in SHULKER_BOXES and 'components' in item:
                container = item['components']['minecraft:container']
                for slot in container:
                    inner = slot['item']
                    inner_id = inner['id'].value
                    if inner_id != 'minecraft:air':
                        counts[inner_id] = counts.get(inner_id, 0) + inner['count'].value
        except Exception:
            continue
    return counts


def _scan_regions(region_names):
    all_counts = {}
    for region_name in region_names:
        region_file = os.path.join(REGION_CACHE, f'{region_name}.mca')
        try:
            rf = nbt_region.RegionFile(region_file)
        except Exception as e:
            log(f"storage: failed to open {region_name}: {e}")
            continue
        for chunk in rf.iter_chunks():
            try:
                block_entities = chunk['Level']['TileEntities'] if 'Level' in chunk else chunk['block_entities']
            except KeyError:
                continue
            for be in block_entities:
                if 'Items' not in be:
                    continue
                try:
                    for item_id, count in _count_items(be['Items']).items():
                        all_counts[item_id] = all_counts.get(item_id, 0) + count
                except Exception:
                    continue
    return all_counts


def _format_name(item_id):
    return item_id.replace('minecraft:', '').replace('_', ' ').title()


def sync_storage(region_lock):
    # the lock is shared with /storage; never wait on it forever
    if not region_lock.acquire(timeout=120):
        raise RuntimeError('region lock still held after 120s (another command is using the region cache)')
    try:
        _cache_regions(MS_REGIONS)
        ms = _scan_regions(MS_REGIONS)
        _cache_regions(DIG_REGIONS)
        dig = _scan_regions(DIG_REGIONS)
    finally:
        region_lock.release()

    if not ms and not dig:
        raise RuntimeError('storage scan found no items at all, keeping the old files')

    # Item icons (<website_path>/cache/items/<id>.png) are rendered from the
    # client jar by tools/render_items.py, not fetched here.

    for fname, counts in (('ms_storage.json', ms), ('dig_storage.json', dig)):
        if not counts:
            log(f"storage: {fname} scan was empty, keeping the old file")
            continue
        out = {_format_name(k): v for k, v in sorted(counts.items(), key=lambda x: x[1], reverse=True)}
        write_json_atomic(os.path.join(DATA_DIR, fname), out)
    log(f"storage: {len(ms)} MS item types, {len(dig)} DIG item types")


# ---------- server status ----------

def _ping(item):
    name, port = item
    try:
        with Client('127.0.0.1', int(port), passwd=_server_cfg['rcon_pass'], timeout=1.5) as c:
            c.run('ping')
        return name, 'Online'
    except Exception:
        return name, 'Offline'


def export_server_status():
    ports = {k.replace('_rcon_port', ''): v for k, v in _server_cfg.items() if k.endswith('_rcon_port')}
    if not ports:
        raise RuntimeError('no *_rcon_port keys in config.json server section')
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(ports)) as ex:
        result = dict(ex.map(_ping, ports.items()))
    write_json_atomic(os.path.join(DATA_DIR, 'server_status.json'), dict(sorted(result.items())))


# ---------- forum archive ----------

# Discord attachment links expire (signed `ex=` param), so archived pictures
# disappeared after a while. Every picture/file/avatar of an archived post is
# downloaded once into the website's cache (next to cache/items, already
# served) and archive.json points at the local copy. The file name comes from
# the URL without its query, so a re-signed link of the same attachment maps
# to the same file and is never downloaded twice.
ARCHIVE_MEDIA_DIR = os.path.join(_server_cfg['website_path'], 'cache', 'archive')
ARCHIVE_MEDIA_URL = '/cache/archive/'
ARCHIVE_MEDIA_MAX_BYTES = 50 * 1024 * 1024   # bigger files (long videos) stay linked to Discord
os.makedirs(ARCHIVE_MEDIA_DIR, exist_ok=True)


def _media_name(url):
    """'<hash>/<original file name>': the folder keeps names unique, the file
    name stays the original one (the archive page shows it as the download
    name and checks its extension for images)."""
    parts = urllib.parse.urlsplit(url)
    base = urllib.parse.unquote(parts.path.rsplit('/', 1)[-1])
    base = re.sub(r'[^A-Za-z0-9._-]', '_', base).strip('._') or 'file'
    digest = hashlib.sha1((parts.netloc + parts.path).encode()).hexdigest()[:16]
    return f'{digest}/{base[:100]}'


def _is_remote(url):
    return isinstance(url, str) and url.startswith(('http://', 'https://'))


def cache_media(url, failed):
    """The local URL of a remote media file (downloaded once), or the URL
    unchanged when it can't be. `failed`: set of media names given up on for
    good (too big, or HTTP 403/404/410), kept in sync_state.json and not
    retried; a timeout or other error is retried at the next sync."""
    if not _is_remote(url):
        return url
    name = _media_name(url)
    dest = os.path.join(ARCHIVE_MEDIA_DIR, name)
    if os.path.exists(dest):
        return ARCHIVE_MEDIA_URL + name
    if name in failed:
        return url
    tmp = dest + '.part'
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with requests.get(url, timeout=20, stream=True) as r:
            if r.status_code != 200:
                if r.status_code in (403, 404, 410):
                    failed.add(name)
                raise ValueError(f'HTTP {r.status_code}')
            size = 0
            with open(tmp, 'wb') as f:
                for chunk in r.iter_content(64 * 1024):
                    size += len(chunk)
                    if size > ARCHIVE_MEDIA_MAX_BYTES:
                        failed.add(name)
                        raise ValueError('too big')
                    f.write(chunk)
        os.replace(tmp, dest)
        return ARCHIVE_MEDIA_URL + name
    except Exception as e:
        log(f"archive: media not cached ({e}): {url.split('?')[0]}")
        try:
            os.remove(tmp)
        except OSError:
            pass
        return url


def _message_media(m):
    """(getter, setter) pairs for every media URL of one archived message/post."""
    out = [(lambda m=m: m.get('author_avatar'), lambda v, m=m: m.__setitem__('author_avatar', v))]
    atts = m.get('attachments', [])
    for i in range(len(atts)):
        out.append((lambda a=atts, i=i: a[i], lambda v, a=atts, i=i: a.__setitem__(i, v)))
    for e in m.get('embeds', []):
        for k in ('image', 'thumbnail'):
            if k in e:
                out.append((lambda e=e, k=k: e[k], lambda v, e=e, k=k: e.__setitem__(k, v)))
    return out


def _post_media(post):
    pairs = _message_media(post)
    for m in post.get('messages', []):
        pairs += _message_media(m)
    return pairs


def localize_post(post, failed):
    """Replaces every remote media URL of a post (and its replies) with the
    cached copy. Runs in a worker thread (blocking downloads)."""
    for get, put in _post_media(post):
        put(cache_media(get(), failed))


def needs_media_refresh(post, failed):
    """True if a saved post still links a remote file that isn't cached and
    wasn't given up on: its links may have expired, so the thread is fetched
    again for fresh ones (once per thread; afterwards it's all local)."""
    for get, _ in _post_media(post):
        url = get()
        if _is_remote(url) and _media_name(url) not in failed \
                and not os.path.exists(os.path.join(ARCHIVE_MEDIA_DIR, _media_name(url))):
            return True
    return False

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {'threads': {}}


def save_state(state):
    tmp = STATE_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


async def fetch_thread_messages(thread):
    messages = []
    # discord.py already waits out rate limits, so no extra sleep per message
    async for msg in thread.history(limit=None, oldest_first=True):
        attachments = [a.url for a in msg.attachments]
        embeds = []
        for e in msg.embeds:
            embed_data = {}
            if e.title:
                embed_data['title'] = e.title
            if e.description:
                embed_data['description'] = e.description
            if e.url:
                embed_data['url'] = e.url
            if e.image:
                embed_data['image'] = e.image.url
            if e.thumbnail:
                embed_data['thumbnail'] = e.thumbnail.url
            embeds.append(embed_data)

        messages.append({
            'id': str(msg.id),
            'author': msg.author.display_name,
            'author_avatar': str(msg.author.display_avatar.url),
            'content': msg.content,
            'timestamp': msg.created_at.isoformat(),
            'attachments': attachments,
            'embeds': embeds,
        })
    return messages


async def sync_archive(bot, full=False):
    if not ARCHIVE_CHANNEL_ID:
        raise RuntimeError('archive_channel_id is not set in config.json')
    channel = bot.get_channel(ARCHIVE_CHANNEL_ID)
    if channel is None:
        raise RuntimeError(f'archive channel {ARCHIVE_CHANNEL_ID} not found (wrong id, or the bot cannot see it)')

    state = load_state()
    existing = {p['id']: p for p in state.get('archive', [])}
    media_failed = set(state.get('archive_media_failed', []))
    now = datetime.now(timezone.utc)

    threads = list(channel.threads)
    async for thread in channel.archived_threads(limit=None):
        threads.append(thread)
    log(f"archive: {len(threads)} threads, {'full' if full else 'incremental'}")

    posts, fetched, failed = [], 0, 0
    for thread in threads:
        tid = str(thread.id)
        tags = [t.name for t in getattr(thread, 'applied_tags', [])]
        last_id = str(thread.last_message_id or '')
        cached = existing.get(tid)

        # unchanged thread (no new messages) whose files are all cached locally:
        # reuse the saved copy instead of refetching it
        if (cached and not full and cached.get('last_message_id') == last_id
                and not needs_media_refresh(cached, media_failed)):
            cached['title'] = thread.name
            cached['tags'] = tags
            # its remote links are all cached (maybe by another thread) or given
            # up on: point them at the local copies; nothing is downloaded here
            localize_post(cached, media_failed)
            posts.append(cached)
            continue

        try:
            messages = await fetch_thread_messages(thread)
        except Exception as e:
            failed += 1
            log(f"archive: thread '{thread.name}' failed: {e!r}")
            if cached:
                posts.append(cached)
            continue

        fetched += 1
        first = messages[0] if messages else {}
        post = {
            'id': tid,
            'title': thread.name,
            'tags': tags,
            'author': first.get('author', ''),
            'author_avatar': first.get('author_avatar', ''),
            'timestamp': thread.created_at.isoformat(),
            'last_message_id': last_id,
            'cached_at': now.isoformat(),
            'content': first.get('content', ''),
            'attachments': first.get('attachments', []),
            'embeds': first.get('embeds', []),
            'messages': messages[1:],
        }
        await asyncio.to_thread(localize_post, post, media_failed)  # downloads: off the event loop
        posts.append(post)
        await asyncio.sleep(0.25)

    posts.sort(key=lambda x: x['timestamp'], reverse=True)
    state['archive'] = posts
    state['archive_media_failed'] = sorted(media_failed)
    save_state(state)
    write_json_atomic(os.path.join(DATA_DIR, 'archive.json'), posts)
    log(f"archive: {len(posts)} posts written ({fetched} fetched, {failed} failed and kept old copy)")


# ---------- cog ----------

class sync(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        if not hasattr(bot, 'region_lock'):
            bot.region_lock = threading.Lock()  # shared with /storage
        self.locks = {n: asyncio.Lock() for n in STEP_TIMEOUT}
        self.heavy_loop.start()
        self.status_loop.start()

    def cog_unload(self):
        self.heavy_loop.cancel()
        self.status_loop.cancel()

    async def run(self, name, full=False):
        """Run one step unless it is already running. Returns None if ok, else an error string."""
        lock = self.locks[name]
        if lock.locked():
            return 'already running'
        async with lock:
            if name == 'storage':
                factory = lambda: asyncio.to_thread(sync_storage, self.bot.region_lock)
            elif name == 'archive':
                factory = lambda: sync_archive(self.bot, full=full)
            else:
                factory = lambda: asyncio.to_thread(export_server_status)
            return await run_step(self.bot, name, factory, STEP_TIMEOUT[name])

    @tasks.loop(hours=HEAVY_EVERY_HOURS)
    async def heavy_loop(self):
        log("scheduled storage + archive sync starting")
        await self.run('storage')
        await self.run('archive')

    @heavy_loop.before_loop
    async def _before_heavy(self):
        await self.bot.wait_until_ready()

    @heavy_loop.error
    async def _heavy_error(self, exc):
        log(f"heavy_loop crashed: {exc!r}, restarting in 30s")
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        asyncio.get_running_loop().call_later(30, lambda: self.heavy_loop.is_running() or self.heavy_loop.start())

    @tasks.loop(seconds=STATUS_EVERY_SECONDS)
    async def status_loop(self):
        await self.run('status')

    @status_loop.before_loop
    async def _before_status(self):
        await self.bot.wait_until_ready()

    @status_loop.error
    async def _status_error(self, exc):
        log(f"status_loop crashed: {exc!r}, restarting in 30s")
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        asyncio.get_running_loop().call_later(30, lambda: self.status_loop.is_running() or self.status_loop.start())

    @commands.hybrid_command(
        name='sync',
        description='Sync storage, server status, forum archive and member list to the website (admin only)',
        help='Sync data to the website, Usage: `!!sync [full]` (Admin Only)'
    )
    @app_commands.describe(mode='incremental = only refetch changed forum threads, full = refetch every thread (slow)')
    @app_commands.default_permissions(administrator=True)
    @commands.has_permissions(administrator=True)
    async def sync(self, ctx, mode: Literal['incremental', 'full'] = 'incremental'):
        await ctx.defer()
        full = mode == 'full'

        async def reply(text):
            # a slash command's reply window is 15 minutes, a long sync can outlive it
            try:
                await ctx.send(text)
            except discord.HTTPException:
                await ctx.channel.send(text)

        await reply(f"⏳ Syncing {'(full)' if full else '(incremental)'}...")

        results = {
            'storage': await self.run('storage'),
            'archive': await self.run('archive', full=full),
            'status': await self.run('status'),
        }
        members_cog = self.bot.get_cog('membersExport')
        results['members'] = await members_cog.run_export() if members_cog else 'members cog is not loaded'

        lines = []
        for name, err in results.items():
            if err is None:
                lines.append(f"✅ {name}")
            elif err == 'already running':
                lines.append(f"⏭ {name}: already running, skipped")
            else:
                lines.append(f"❌ {name}: {err}")
        await reply('\n'.join(lines))

    @commands.hybrid_command(
        name='sync_status',
        description='Show when each website data sync last ran (admin only)',
        help='Show website sync health, Usage: `!!sync_status` (Admin Only)'
    )
    @app_commands.default_permissions(administrator=True)
    @commands.has_permissions(administrator=True)
    async def sync_status(self, ctx):
        health = getattr(self.bot, 'sync_health', {})
        lines = []
        for name in ('storage', 'archive', 'status', 'members'):
            s = health.get(name)
            if not s:
                lines.append(f"• {name}: has not run since the bot started")
                continue
            last_ok = f"<t:{int(s['last_ok'])}:R>" if s.get('last_ok') else 'never'
            line = f"{'✅' if s['ok'] else '❌'} {name}: last success {last_ok}, last run took {s['seconds']}s"
            if not s['ok']:
                line += f"\n    error: {s['error']}"
            lines.append(line)

        members_cog = self.bot.get_cog('membersExport')
        loops = [('storage+archive loop', self.heavy_loop), ('status loop', self.status_loop)]
        if members_cog:
            loops.append(('members loop', members_cog.export_loop))
        lines.append('')
        for label, loop in loops:
            nxt = loop.next_iteration
            when = f"<t:{int(nxt.timestamp())}:R>" if nxt else 'unknown'
            lines.append(f"{'🟢' if loop.is_running() else '🔴 NOT RUNNING'} {label}, next run {when}")
        await ctx.send('\n'.join(lines))


async def setup(bot):
    await bot.add_cog(sync(bot))

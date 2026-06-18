# commands/syncCommand.py
import discord
from discord.ext import commands, tasks
import json
import os
import asyncio
import aiohttp
from datetime import datetime, timezone, timedelta
from rcon import Client
import socket
import json as _json
import os
import shutil
from nbt import nbt, region as nbt_region
import requests

DATA_DIR = '/var/www/html/data'
STATE_FILE = '/opt/chronos/Chronos-Bot/sync_state.json'
ARCHIVE_CHANNEL_ID = 1123695308933697647
CACHE_TTL_HOURS = 20

os.makedirs(DATA_DIR, exist_ok=True)

ITEM_CACHE_DIR = '/var/www/html/cache/items'
MC_ASSETS = 'https://raw.githubusercontent.com/InventivetalentDev/minecraft-assets/26.2/assets/minecraft/textures'
os.makedirs(ITEM_CACHE_DIR, exist_ok=True)

# load config directly
with open('/opt/chronos/Chronos-Bot/config.json') as f:
    _cfg = _json.load(f)

_server_cfg = _cfg['server']
MS_REGIONS = _server_cfg['storage_regions']
DIG_REGIONS = _server_cfg['dig_storage_regions']
REGION_SOURCE = os.path.join(_server_cfg['smp_path'], _server_cfg['smp_world_name'], 'dimensions', 'minecraft', 'overworld', 'region')
REGION_CACHE = '/opt/chronos/Chronos-Bot/region-cache'
os.makedirs(REGION_CACHE, exist_ok=True)

SHULKER_BOXES = {
    'minecraft:white_shulker_box','minecraft:orange_shulker_box','minecraft:magenta_shulker_box',
    'minecraft:light_blue_shulker_box','minecraft:yellow_shulker_box','minecraft:lime_shulker_box',
    'minecraft:pink_shulker_box','minecraft:gray_shulker_box','minecraft:light_gray_shulker_box',
    'minecraft:cyan_shulker_box','minecraft:purple_shulker_box','minecraft:blue_shulker_box',
    'minecraft:brown_shulker_box','minecraft:green_shulker_box','minecraft:red_shulker_box',
    'minecraft:black_shulker_box','minecraft:shulker_box'
}

def _fetch_texture(url, dest):
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            with open(dest, 'wb') as f:
                f.write(r.content)
            return True
    except Exception:
        pass
    return False

def cache_item_textures(item_ids):
    for raw_id in item_ids:
        id_ = raw_id.replace('minecraft:', '')
        dest = os.path.join(ITEM_CACHE_DIR, f'{id_}.png')
        if os.path.exists(dest):
            continue
        urls = [
            f'{MC_ASSETS}/item/{id_}.png',
            f'{MC_ASSETS}/block/{id_}.png',
            f'{MC_ASSETS}/block/{id_}_top.png',
            f'{MC_ASSETS}/block/{id_}_side.png',
        ]
        for url in urls:
            if _fetch_texture(url, dest):
                break

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
            print(f"Failed to open {region_name}: {e}")
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

def sync_storage():
    _cache_regions(MS_REGIONS)
    ms = _scan_regions(MS_REGIONS)
    ms_out = {_format_name(k): v for k, v in sorted(ms.items(), key=lambda x: x[1], reverse=True)}

    _cache_regions(DIG_REGIONS)
    dig = _scan_regions(DIG_REGIONS)
    dig_out = {_format_name(k): v for k, v in sorted(dig.items(), key=lambda x: x[1], reverse=True)}

    # cache textures for all unique items
    all_ids = set(ms.keys()) | set(dig.keys())
    cache_item_textures(all_ids)

    with open(os.path.join(DATA_DIR, 'ms_storage.json'), 'w') as f:
        _json.dump(ms_out, f)
    with open(os.path.join(DATA_DIR, 'dig_storage.json'), 'w') as f:
        _json.dump(dig_out, f)
    print(f"Storage sync: {len(ms_out)} MS items, {len(dig_out)} DIG items")

async def export_server_status(bot):
    # reuse RCON_PORTS from storageCommand config
    import json as _json
    f = open('/opt/chronos/Chronos-Bot/config.json')
    cfg = _json.load(f)
    f.close()
    rcon_pass = cfg['server']['rcon_pass']
    ports = {k.replace('_rcon_port',''): v for k,v in cfg['server'].items() if k.endswith('_rcon_port')}
    
    result = {}
    for name, port in ports.items():
        try:
            with Client('127.0.0.1', int(port), passwd=rcon_pass, timeout=1.5) as c:
                c.run('ping')
            result[name] = 'Online'
        except:
            result[name] = 'Offline'
    
    with open(os.path.join(DATA_DIR, 'server_status.json'), 'w') as f:
        json.dump(result, f)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {'threads': {}}


def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)


async def fetch_thread_messages(thread):
    messages = []
    async for msg in thread.history(limit=None, oldest_first=True):
        await asyncio.sleep(0.5)
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
    channel = bot.get_channel(ARCHIVE_CHANNEL_ID)
    if channel is None:
        print(f"Archive channel {ARCHIVE_CHANNEL_ID} not found")
        return

    state = load_state()
    existing = {p['id']: p for p in state.get('archive', [])}
    posts = []
    now = datetime.now(timezone.utc)

    active_threads = channel.threads
    archived_threads = []
    async for thread in channel.archived_threads(limit=None):
        await asyncio.sleep(1)
        archived_threads.append(thread)

    all_threads = list(active_threads) + archived_threads
    print(f"Archive sync: {len(all_threads)} threads found")

    for thread in all_threads:
        thread_id = str(thread.id)
        last_modified = thread.archive_timestamp.isoformat() if thread.archive_timestamp else thread.created_at.isoformat()

        # check if cached version is still fresh enough
        if not full and thread_id in existing:
            cached = existing[thread_id]
            cached_at_str = cached.get('cached_at')
            if cached_at_str:
                cached_at = datetime.fromisoformat(cached_at_str)
                age = now - cached_at
                if age < timedelta(hours=CACHE_TTL_HOURS) and cached.get('last_modified') == last_modified:
                    posts.append(cached)
                    continue

        print(f"Fetching thread: {thread.name}")
        await asyncio.sleep(1)

        messages = await fetch_thread_messages(thread)
        tags = [tag.name for tag in thread.applied_tags] if hasattr(thread, 'applied_tags') else []
        first_msg = messages[0] if messages else {}

        post = {
            'id': thread_id,
            'title': thread.name,
            'tags': tags,
            'author': first_msg.get('author', ''),
            'author_avatar': first_msg.get('author_avatar', ''),
            'timestamp': thread.created_at.isoformat(),
            'last_modified': last_modified,
            'cached_at': now.isoformat(),
            'content': first_msg.get('content', ''),
            'attachments': first_msg.get('attachments', []),
            'embeds': first_msg.get('embeds', []),
            'messages': messages[1:],
        }
        posts.append(post)

    posts.sort(key=lambda x: x['timestamp'], reverse=True)
    state['archive'] = posts
    save_state(state)

    with open(os.path.join(DATA_DIR, 'archive.json'), 'w') as f:
        json.dump(posts, f)

    print(f"Archive sync complete: {len(posts)} posts")


class sync(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.scheduled_sync.start()

    def cog_unload(self):
        self.scheduled_sync.cancel()

    @tasks.loop(hours=8)
    async def scheduled_sync(self):
        await self.bot.wait_until_ready()
        print("Running scheduled sync...")
        try:
            await asyncio.get_event_loop().run_in_executor(None, sync_storage)
            await sync_archive(self.bot, full=False )
            await export_server_status(self.bot)
            members_cog = self.bot.get_cog('membersExport')
            if members_cog:
                await members_cog.export_members()
            print("Scheduled sync complete")
        except Exception as e:
            print(f"Scheduled sync error: {e}")

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def sync(self, ctx, mode=None):
        full = mode == 'full'
        await ctx.send(f"⏳ Syncing {'(full)' if full else '(incremental)'}...")

        try:
            await asyncio.get_event_loop().run_in_executor(None, sync_storage)
        except Exception as e:
            await ctx.send(f"❌ Storage sync failed: {e}")
            return

        try:
            await sync_archive(self.bot, full=full)
        except Exception as e:
            await ctx.send(f"❌ Archive sync failed: {e}")
            return
        
        try:
            await export_server_status(self.bot)
        except Exception as e:
            await ctx.send(f"❌ Status sync failed: {e}")
            return
        
        members_cog = self.bot.get_cog('membersExport')
        if members_cog:
            await members_cog.export_members()

        await ctx.send("✅ Synced storage, status, archive and members.")


async def setup(bot):
    await bot.add_cog(sync(bot))
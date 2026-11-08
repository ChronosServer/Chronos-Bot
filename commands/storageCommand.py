import asyncio
import threading
import discord
from discord import app_commands
from discord.ext import commands
import json
import os
import shutil
from typing import Literal
from nbt import nbt, region

f = open('config.json')
data = json.load(f)
smp_path = data['server']['smp_path']
smp_world_name = data['server']['smp_world_name']
ms_regions = data['server']['storage_regions']
dig_regions = data['server']['dig_storage_regions']
f.close()

REGION_SOURCE = os.path.join(smp_path, smp_world_name, 'dimensions', 'minecraft', 'overworld', 'region')
REGION_CACHE = './region-cache'
os.makedirs(REGION_CACHE, exist_ok=True)

SHULKER_BOXES = {
    'minecraft:white_shulker_box', 'minecraft:orange_shulker_box', 'minecraft:magenta_shulker_box',
    'minecraft:light_blue_shulker_box', 'minecraft:yellow_shulker_box', 'minecraft:lime_shulker_box',
    'minecraft:pink_shulker_box', 'minecraft:gray_shulker_box', 'minecraft:light_gray_shulker_box',
    'minecraft:cyan_shulker_box', 'minecraft:purple_shulker_box', 'minecraft:blue_shulker_box',
    'minecraft:brown_shulker_box', 'minecraft:green_shulker_box', 'minecraft:red_shulker_box',
    'minecraft:black_shulker_box', 'minecraft:shulker_box'
}


def format_item_name(item_id):
    name = item_id.replace('minecraft:', '')
    return name.replace('_', ' ').title()


def format_count(count):
    return f"{count:,} ({count / 1_000_000:.2f}M)"

def count_items_in_list(items, target):
    total = 0
    for item in items:
        item_id = item['id'].value
        count = item['count'].value if 'count' in item else 1
        if item_id == target:
            total += count
        if item_id in SHULKER_BOXES and 'components' in item:
            try:
                container = item['components']['minecraft:container']
                for slot in container:
                    inner_item = slot['item']
                    if inner_item['id'].value == target:
                        total += inner_item['count'].value
            except (KeyError, TypeError):
                pass
    return total


def count_all_items_in_list(items):
    counts = {}
    for item in items:
        item_id = item['id'].value
        count = item['count'].value if 'count' in item else 1
        if item_id != 'minecraft:air':
            counts[item_id] = counts.get(item_id, 0) + count
        if item_id in SHULKER_BOXES and 'components' in item:
            try:
                container = item['components']['minecraft:container']
                for slot in container:
                    inner_item = slot['item']
                    inner_id = inner_item['id'].value
                    if inner_id != 'minecraft:air':
                        counts[inner_id] = counts.get(inner_id, 0) + inner_item['count'].value
            except (KeyError, TypeError):
                pass
    return counts


def cache_regions(region_names):
    os.makedirs(REGION_CACHE, exist_ok=True)
    for region_name in region_names:
        src = os.path.join(REGION_SOURCE, f'{region_name}.mca')
        dst = os.path.join(REGION_CACHE, f'{region_name}.mca')
        tmp = dst + '.tmp'
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)  # atomic rename


def scan_regions(region_names, target_item=None):
    total = 0
    all_counts = {}
    for region_name in region_names:
        region_file = os.path.join(REGION_CACHE, f'{region_name}.mca')
        try:
            rf = region.RegionFile(region_file)
        except Exception as e:
            print(f"Failed to open region {region_name}: {e}")
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
                    if target_item:
                        total += count_items_in_list(be['Items'], target_item)
                    else:
                        for item_id, count in count_all_items_in_list(be['Items']).items():
                            all_counts[item_id] = all_counts.get(item_id, 0) + count
                except Exception as e:
                    print(f"Failed to process block entity in {region_name}: {e}")
                    continue
    return total if target_item else all_counts


def scan_with_lock(region_lock, region_names, target_item):
    # one thread at a time may touch the region cache, /sync uses the same files
    with region_lock:
        cache_regions(region_names)
        return scan_regions(region_names, target_item=target_item)


class storage(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        if not hasattr(bot, 'region_lock'):
            bot.region_lock = threading.Lock()  # shared with /sync

    @commands.hybrid_command(
        name='storage',
        description='Count an item in Main/Dig Storage, or show the 10 most stored items',
        help='Show amount of items stored, Usage: `!!storage <ms/dig/all> <item_id/top>`'
    )
    @app_commands.describe(
        location='Which storage to scan: ms = Main Storage, dig = Dig Storage, all = both',
        item='Item id to count (e.g. diamond_block), or "top" for the 10 most stored items'
    )
    async def storage(self, ctx, location: Literal['ms', 'dig', 'all'], item: str):
        await ctx.defer()
        if location == 'ms':
            regions = ms_regions
            storage_label = 'Main Storage'
        elif location == 'dig':
            regions = dig_regions
            storage_label = 'Dig Storage'
        else:
            regions = ms_regions + dig_regions
            storage_label = 'Main Storage & Dig Storage'

        target = None if item == 'top' else (item if ':' in item else f'minecraft:{item}')

        # copying and scanning the region files is heavy, so it runs in a thread
        result = await asyncio.to_thread(scan_with_lock, self.bot.region_lock, regions, target)

        if target is None:
            top10 = sorted(result.items(), key=lambda x: x[1], reverse=True)[:10]
            embed = discord.Embed(title=f'Top 10 items in {storage_label}')
            for item_id, count in top10:
                embed.add_field(name=format_item_name(item_id), value=format_count(count), inline=False)
            embed.set_footer(text='Chronos™')
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(description=f'**{format_count(result)}** {format_item_name(target)}')
            embed.set_footer(text='Chronos™')
            await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(storage(bot))
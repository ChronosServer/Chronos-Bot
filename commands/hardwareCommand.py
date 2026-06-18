import discord
from discord.ext import commands
import psutil
import json
import datetime
import os

f = open('config.json')
data = json.load(f)
cpu_hardware = data['server']['cpu_hardware']
ram_hardware = data['server']['ram_hardware']
f.close()


def fmt_gb(b):
    return round(b / 1073741824, 2)

def fmt_tb(b):
    return round(b / 1099511627776, 2)


class hardware(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(help='Display hardware usage, Usage: `!!hardware`')
    async def hardware(self, ctx):
        cpu_percent = psutil.cpu_percent(interval=0.5)
        cpu_cores = psutil.cpu_count(logical=False)
        cpu_threads = psutil.cpu_count(logical=True)
        load1, load5, load15 = os.getloadavg()

        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()

        disk = psutil.disk_usage('/')
        disk_io = psutil.disk_io_counters()

        net = psutil.net_io_counters()

        uptime_seconds = datetime.datetime.now().timestamp() - psutil.boot_time()
        uptime = str(datetime.timedelta(seconds=int(uptime_seconds)))

        temps = psutil.sensors_temperatures()
        cpu_temp = None
        for key in ('coretemp', 'k10temp', 'cpu_thermal'):
            if key in temps:
                cpu_temp = round(temps[key][0].current, 1)
                break

        embed = discord.Embed(title='Hardware Usage')

        embed.add_field(
            name=f'CPU — {cpu_hardware}',
            value=(
                f'**{cpu_percent}%** | {cpu_cores}C / {cpu_threads}T\n'
                f'Load avg (1/5/15 min): {load1:.2f} {load5:.2f} {load15:.2f}\n'
                f'Uptime: {uptime}'
                + (f' | Temp: {cpu_temp}°C' if cpu_temp else '')
            ),
            inline=False
        )
        embed.add_field(
            name=f'RAM — {ram_hardware}',
            value=f'**{fmt_gb(vm.used)} / {fmt_gb(vm.total)} GB ({vm.percent}%)**',
            inline=False
        )
        embed.add_field(
            name='Swap (overflow RAM on disk)',
            value=f'**{fmt_gb(swap.used)} / {fmt_gb(swap.total)} GB ({swap.percent}%)**',
            inline=False
        )
        embed.add_field(
            name='Disk',
            value=(
                f'**{fmt_gb(disk.used)} Gb ({fmt_tb(disk.used)} Tb) / {fmt_gb(disk.total)} Gb ({fmt_tb(disk.total)} Tb) ({disk.percent}%)**\n'
                f'I/O since boot: ↑ {fmt_gb(disk_io.write_bytes)} Gb ({fmt_tb(disk_io.write_bytes)} Tb)  ↓ {fmt_gb(disk_io.read_bytes)} Gb ({fmt_tb(disk_io.read_bytes)} Tb)'
            ),
            inline=False
        )
        embed.add_field(
            name='Network (since boot)',
            value=f'↑ {fmt_gb(net.bytes_sent)} GB  ↓ {fmt_gb(net.bytes_recv)} GB',
            inline=False
        )
        embed.set_footer(text='Chronos™')
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(hardware(bot))
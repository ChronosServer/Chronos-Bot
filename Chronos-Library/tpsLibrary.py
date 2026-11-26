import os
from nbt import nbt


def tps_check(server_path):
    """Average TPS over the last autosave interval (300 s = 6000 ticks at 20 TPS),
    from the LastPlayed times of level.dat and level.dat_old. Never raises: /tps
    gathers every server at once, so one unreadable file (e.g. level.dat being
    saved right now) must not fail the whole command."""
    try:
        last_played = []
        for file in ['level.dat', 'level.dat_old']:
            last_played.append(nbt.NBTFile(os.path.join(server_path, file))['Data']['LastPlayed'].value)
        elapsed = (last_played[0] - last_played[1]) / 1000.0
        if elapsed <= 0:
            return 'N/A'
        tps = 300.0 / elapsed * 20.0
        return str(round(min(max(tps, 0.0), 20.0), 2))
    except FileNotFoundError:
        return 'N/A'
    except Exception as e:
        print(f'tps_check {server_path}: {e!r}')
        return 'N/A (unreadable)'

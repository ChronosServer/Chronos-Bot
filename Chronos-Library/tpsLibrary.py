import os
from nbt import nbt

def tps_check(server_path):
    last_played = []
    for file in ['level.dat', 'level.dat_old']:
            last_played.append(nbt.NBTFile(os.path.join(server_path, file))['Data']['LastPlayed'].value)
    tps = 300.0 / ((last_played[0] - last_played[1]) / 1000.0) * 20.0
    if tps > 20.0:
            tps = 20.0
    if tps < 0.0:
        tps = 0.0
    return str(round(tps, 2))
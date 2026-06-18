# statusLibrary.py
import socket
from rcon import Client

def status_check(rcon_port, rcon_password):
    try:
        with Client('127.0.0.1', int(rcon_port), passwd=rcon_password, timeout=1.5) as client:
            response = client.run('ping')
            return 'Online' if response else 'Online'
    except socket.timeout:
        return 'Response took too long'
    except ConnectionError:
        return 'Offline'

def storage_check(check_coords, rcon_port, rcon_password):
    try:
        with Client('127.0.0.1', int(rcon_port), passwd=rcon_password, timeout=1.5) as client:
            response = client.run(f'execute if block {check_coords} minecraft:redstone_block')
            if response == 'Test passed':
                return 'Running'
            elif response == 'Test failed':
                return 'Idle'
            return response
    except socket.timeout:
        return 'Offline'
import socket
from rcon import Client
import errno

def status_check(rcon_port, rcon_password):
    try:
        with Client('127.0.0.1', int(rcon_port), passwd=rcon_password, timeout=1.5) as client:
            response = client.run('ping')
            if response != '':
                response = 'Online'
            else:
                pass
            return response
    except socket.timeout:
            response = 'Response took too long'
            return response
    except ConnectionError:
        response = 'Offline'
        return response

def storage_check(check_coords, rcon_port2, rcon_password2):
    try:
        with Client('127.0.0.1', int(rcon_port2), passwd=rcon_password2, timeout=1.5) as client:
            storage_response = client.run('execute if block ' + check_coords + ' minecraft:redstone_block')
            if storage_response == 'Test passed':
                storage_response = 'Running'
            elif storage_response == 'Test failed':
                storage_response = 'Idle'
            return storage_response
    except socket.timeout:
            return
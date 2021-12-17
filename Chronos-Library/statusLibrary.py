import socket
from rcon import Client

def status_check(rcon_port, rcon_password):
    try:
        with Client('127.0.0.1', int(rcon_port), passwd=rcon_password, timeout=1.5) as client:
            response = None
            response = client.run('ping')
            if response == '':
                response = 'Offline'
            elif response != '':
                response = 'Online'
            return response
    except socket.timeout:
        response = "Couldn't reach the server in time" 
        return response

def storage_check(check_coords, rcon_port2, rcon_password2):
    try:
        with Client('127.0.0.1', int(rcon_port2), passwd=rcon_password2, timeout=1.5) as client:
            storage_response = None
            storage_response = client.run('execute if block ' + check_coords + ' minecraft:redstone_block')
            if storage_response == 'Test passed':
                storage_response = 'Running'
            elif storage_response == 'Test failed':
                storage_response = 'Idle'
            return storage_response
    except socket.timeout as timeout:
        response = "Couldn't reach the server in time" 
        return response
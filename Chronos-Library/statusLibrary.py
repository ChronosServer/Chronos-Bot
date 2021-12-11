from rcon import Client

def status_check(rcon_port, rcon_password):
    with Client('127.0.0.1', int(rcon_port), passwd=rcon_password) as client:
        response = None
        response = client.run('ping')
        if response == '':
            response = 'Offline'
        elif response != '':
            response = 'Online'
    return response

def storage_check(check_coords, rcon_port2, rcon_password2):
    with Client('127.0.0.1', int(rcon_port2), passwd=rcon_password2) as client:
        storage_response = None
        storage_response = client.run('execute if block ' + check_coords + ' minecraft:redstone_block')
        if storage_response == 'Test passed':
            storage_response = 'Running'
        elif storage_response == 'Test failed':
            storage_response = 'Idle'
    return storage_response
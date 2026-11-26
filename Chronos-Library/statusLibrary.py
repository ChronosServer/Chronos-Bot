# statusLibrary.py
import socket
from rcon import Client
from rcon.exceptions import WrongPassword

# Both checks run in worker threads (asyncio.to_thread) and never raise: /status
# gathers every server at once, so one exception would fail the whole command
# (this happened for the storage check whenever the SMP was offline).


def _error_text(e):
    if isinstance(e, socket.timeout):
        return 'Response took too long'
    if isinstance(e, ConnectionError):
        return 'Offline'
    if isinstance(e, WrongPassword):
        return 'RCON password rejected'
    return f'Error ({type(e).__name__})'


def status_check(rcon_port, rcon_password):
    try:
        with Client('127.0.0.1', int(rcon_port), passwd=rcon_password, timeout=1.5) as client:
            client.run('ping')
            return 'Online'
    except Exception as e:
        return _error_text(e)


def storage_check(check_coords, rcon_port, rcon_password):
    try:
        with Client('127.0.0.1', int(rcon_port), passwd=rcon_password, timeout=1.5) as client:
            response = client.run(f'execute if block {check_coords} minecraft:redstone_block')
            if response == 'Test passed':
                return 'Running'
            elif response == 'Test failed':
                return 'Idle'
            return response or 'No response'
    except Exception as e:
        return _error_text(e)

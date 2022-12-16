from rcon import Client
import socket

async def defaultRcon(rcon_port, rcon_pass, mccmd):
    try:
        with Client('127.0.0.1', int(rcon_port), passwd=rcon_pass, timeout=1.5) as client:
            response = client.run(mccmd)
            if response == '':
                print
            elif response != '':
                return response
    except socket.timeout:
            return "Couldn't reach the server in time"
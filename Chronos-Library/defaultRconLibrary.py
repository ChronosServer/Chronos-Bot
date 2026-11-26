import asyncio
import socket

from rcon import Client
from rcon.exceptions import WrongPassword


def rcon_run(rcon_port, rcon_pass, mccmd):
    """One RCON command (blocking). The response text, None for an empty
    response, or a readable error message; never raises."""
    try:
        with Client('127.0.0.1', int(rcon_port), passwd=rcon_pass, timeout=1.5) as client:
            response = client.run(mccmd)
            if response:
                return response
    except socket.timeout:
        return "Couldn't reach the server in time"
    except ConnectionError:
        return "Server is offline or RCON is unavailable"
    except WrongPassword:
        return "RCON password rejected"
    except Exception as e:
        return f"RCON error ({type(e).__name__}: {e})"


async def defaultRcon(rcon_port, rcon_pass, mccmd):
    # in a worker thread: the RCON socket blocks, and blocking the event loop
    # stalls the whole bot (Discord heartbeat included)
    return await asyncio.to_thread(rcon_run, rcon_port, rcon_pass, mccmd)

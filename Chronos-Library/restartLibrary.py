from rcon import Client
import socket
import parse
from socket import error as socket_error
import errno

def rcon(rcon_port, rcon_pass, cmd):
    try:
        with Client('127.0.0.1', int(rcon_port), passwd=rcon_pass, timeout=1.5) as client:
            response = client.run(str(cmd))
            if response == '':
                pass
            elif response != '':
                return response
    except socket.timeout:
        pass
    except socket_error as serr:
        if serr.errno != errno.ECONNREFUSED:
            raise serr
        pass

def handle_list(response):
		formatters = (
			r'There are {amount:d} of a max {limit:d} players online:{players}',  # <1.16
			r'There are {amount:d} of a max of {limit:d} players online:{players}',  # >=1.16
		)
		for formatter in formatters:
			parsed = parse.parse(formatter, response)
			if parsed is not None and parsed['players'].startswith(' '):
				players = parsed['players'][1:]
				if len(players) > 0:
					return(players.split(', '))
				else:
					pass
				break

def read_log(server_path):
    with open(server_path + '/logs/latest.log', 'r') as f:
        last_line = f.readlines()[-2]
        last_line = last_line[40:]
        log_line = last_line.split(', ')
        log_line[-1] = log_line[-1].strip()
    return log_line
# Chronos discord bot

## Description

The bot features various different commands that can display information about the server and more. 

The bot is still a work in progress, some commands may not fully function or they may contain issues.

### Dependencies

* Any os, Linux & Windows run scripts are included
* Python 3.12 or newer (developed on 3.13, the live server runs 3.14)
* discord.py, rcon, psutil, nbt and the rest of `requirements.txt`
* Install requirements with running $ pip install -r requirements.txt
* The bot no longer uses `nest_asyncio` (it breaks on Python 3.14); `main.py` starts the client with `client.start()` inside `asyncio.run(main())`

### Installing
* Clone the repository with $ git clone https://github.com/ChronosServer/Chronos-Bot.git or download and unzip the latest release
* Fill out config.json
* Optinal: edit some of the command files to contain your server's information

### Running the bot

* Windows: Run the start.bat file
* Linux: ./start.sh (has auto-restart)

## Stats tracking (website Stats pages)

Besides the Discord bot, this repo holds the backend for the Chronos website's Stats
section:

* `bot/snapshot.py` reads every tracked world's player stat files and `scoreboard.dat`
  directly (no RCON) and stores only the values that changed in a SQLite database.
  Configure it with the `stats_tracking` block in `config.json`. It runs every 15
  minutes via `deploy/systemd/chronos-stats-snapshot.timer`.
* After each snapshot, `bot/derive.py` works out activity, server totals, milestones,
  firsts, badges, rivalries and the stat spotlight.
* `api/` is a read-only FastAPI service (`deploy/systemd/chronos-stats-api.service`,
  127.0.0.1:8765) that nginx exposes at `/api/stats/` (`deploy/nginx/`).
* `STATS_PLAN.md` is the feature scope, `STATS_API.md` the HTTP contract the website
  builds against, and `CLAUDE.md` the full technical notes (rules, deploy order).

Run the tests with:

    pip install -r requirements.txt -r requirements-dev.txt
    python -m pytest tests -q

When deploying the API on a server that already runs the bot, install only the three
API packages (`fastapi`, `uvicorn[standard]`, `orjson`) and run `bot/snapshot.py` once
before starting the API. See `CLAUDE.md`.

## Help

If you run into any issues, please contact chezloc on discord. Or join the [Chronos Discord](https://discord.gg/VvPucVAjUS)

## Authors

- chezloc

## Commands

The bot's help command contains additional information on how to use commands 

* Admin only
        - setstatus | allows you to change the bot's discord status
        - color | allows assignment of in game color via scoreboard teams
        - execute | allows you to execute a command via rcon
        - reload | reload the bot
        - ban | ban user with user id
        - purge | delete selected amount of messages
        - statTransfer | transfer stats from one player to another
        - webserver | upload to the webserver
* Member only
    * The bot member role id can be specified in config.json
        - region | sends specified region file over discord
        - structure | allows you to download/upload structure files from cmp
        - region | allows you to download region files from servers
* Public
    * Commands available for normal user
        - hardware | displays usage of hardware
        - help | shows help command
        - ping | displays bot ping
        - worldsize | displays world size information   
        - hardware | displays hardware information
        - calc | a calculator
        - tps | shows server tps

## Version History
* 1.0
    * Initial Release
* Stats backend
    * Change-only stats snapshots, derived achievements and the read-only stats API for the website
    * Python 3.14 support (`nest_asyncio` removed)
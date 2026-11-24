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
* **Setting up the database:** see [docs/STATS_DATABASE.md](docs/STATS_DATABASE.md).
  It covers configuration, first setup, upgrades, health checks, backup and reset.
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

Every command is a hybrid command: it works as a slash command (`/ping`), by mentioning
the bot (`@Chronos ping`) and with the text prefix (`!!ping`). Which of the three are on
is set in `config.json` (`bot.slash_commands`, `bot.mention_prefix`, `bot.text_prefix`;
only the text prefix needs the Message Content intent). Slash commands are registered to
the Chronos guild only. `help` lists everything.

* Admin only (Discord administrator; `ban` needs Ban Members)
    - ban | ban a user by ID, optionally deleting their recent messages
    - color | change a player's color on the SMP (scoreboard teams), or list the colors
    - execute | run a console command on a server via RCON
    - purge | delete the most recent messages in the channel
    - reload | reload all command files and re-sync slash commands
    - restart | restart a server, optionally respawning its bots afterwards
    - setstatus | change the bot's status text
    - stattransfer | move all scoreboard stats from one player to another
    - sync | sync storage, server status, forum archive and member list to the website now
    - sync_members | re-export the member directory to the website now
    - sync_status | show when each website sync last ran and when it runs next
    - webserver | upload, delete or list files on the website file server
    - whitelist | add, remove or list whitelisted players (single name or a .txt file)
* Members only (member, trial member or inactive member role from `config.json`)
    - region | download a region file and its entities file from a server
    - setdisplay | show a scoreboard in the SMP sidebar
    - structure | download, upload or list structure (.nbt) files on a server
* Public
    - calc | evaluate a math expression
    - hardware | CPU, RAM, swap, disk and network usage of the host
    - help | list commands
    - online | which players are online on each server
    - ping | bot latency
    - stats | leaderboards from player stats and scoreboards
    - status | which servers are online, and whether Main Storage is running
    - storage | count an item in Main/Dig Storage, or show the 10 most stored items
    - tps | each server's average TPS over the last 300 seconds
    - worldsize | size on disk of every world, the SMP recordings and the webserver

Background jobs (no command needed): server status to the website every 5 minutes,
storage scan + forum archive every 8 hours, member directory every hour, and crash
report alerts posted to the crash log channel as soon as a server writes one.

## Version History
* 1.0
    * Initial Release
* Stats backend
    * Change-only stats snapshots, derived achievements and the read-only stats API for the website
    * Python 3.14 support (`nest_asyncio` removed)
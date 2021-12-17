# Chronos Discord Bot

## Description

The bot features various different commands that can display information about the server and more. 

The bot is still a work in progress, some commands may not fully function or they may contain issues.

### Dependencies

* Any os, Linux & Windows run scripts are included
* [Python 3.9 or newer](https://www.python.org/downloads/)
* [discord.py](https://pypi.org/project/discord.py/)
* [rcon](https://pypi.org/project/rcon/)
* [psutil](https://pypi.org/project/psutil/)
* [NBT](https://pypi.org/project/NBT/)
* Install requirements with running $ pip install -r requirements.txt

### Installing
* Clone the repository with $ git clone https://github.com/ChronosServer/Chronos-Bot.git or download and unzip the latest release
* Fill out config.json
* Optinal: edit some of the command files to contain your server's information

### Running the bot

* Windows: Run the start.bat file
* Linux: ./start.sh (has auto-restart)

## Help

If you run into any issues, please contact Chezloc#2039 on discord. Or join the [Chronos Discord](https://discord.gg/VvPucVAjUS)

## Authors

- Chezloc#2039 

## Commands

The bot's help command contains additional information on how to use commands 

* Admin only
    * Commands only available for admins
        - setstatus | allows you to change the bot's discord status
        - color | allows assignment of in game color via scoreboard teams
        - execute | allows you to execute a command via rcon
        - reload | reload the bot
        - ban | ban user with user id
        - purge | delete selected amount of messages
* Member only
    * The bot member role id can be specified in config.json
        - region | sends specified region file over discord
        - structure | allows you to download/upload structure files from cmp
* Public
    * Commands available for normal user
        - hardware | displays usage of hardware
        - help | shows help command
        - ping | displays bot ping
        - worldsize | displays world size information  
        - tps | shows server tps
        - status | shows server status

## Version History
* 1.1
    * Add status and tps command, code cleanup
* 1.0
    * Initial Release

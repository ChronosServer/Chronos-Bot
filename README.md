# Chronos discord bot

## Description

The bot features various different commands that can display information about the server and more. 

The bot is still a work in progress, some commands may not fully function or they may contain issues.

### Dependencies

* Any os, Linux & Windows run scripts are included
* Python 3.9 or newer
* discord.py
* rcon
* psutil
* Install requirements with running $ pip install -r requirements.txt

### Installing
* Clone the repository with $ git clone https://github.com/ChronosServer/Chronos-Bot.git or download and unzip the latest release
* Fill out config.json
* Optinal: edit some of the command files to contain your server's information

### Running the bot

* Windows: Run the start.bat file
* Linux: ./start.sh (has auto-restart)

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
# SnitchVisBot

A discord bot for visualizing [snitch](https://civwiki.org/wiki/Snitch) events from [Civ](https://civwiki.org/wiki/Main_Page) servers. Written for [CivMC](https://old.reddit.com/r/CivMC). Uses [SnitchVis](https://github.com/tybug/snitchvis) to render the videos.


## Commands

| Command          | Description                                                                                                                    |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| [.render](#render)          | Renders snitch events to a video. Provides options to adjust render look and feel, events included, duration, quality, etc.    |
| [.tutorial](#tutorial)        | Walks you through an initial setup of snitchvis.                                                                               |
| [.channel add](#channel-add)     | Adds a snitch channel(es), viewable by the specified roles.                                                                    |
| [.channel list](#channel-list)    | Lists the current snitch channels and what roles can view them.                                                                |
| [.channel remove](#channel-remove) | Removes a snitch channel(es) from the list of snitch channels.                                                                 |
| [.events](#events)          | Lists the most recent events for the specified snitch or snitches.                                                             |
| [.full-reindex](#full-reindex)    | Drops all currently indexed snitches and re-indexes from scratch.                                                              |
| [.import-snitches](#import-snitches) | Imports snitches from a SnitchMod database.                                                                                    |
| [.index](#index)          | Indexes messages in the current snitch channels.                                                                               |
| [.permissions](#permissions)     | Lists what snitch channels you have permission to render events from.                                                          |
| [.help](#help)            | Displays available commands.                                                                                                   |


## Command Options

### Render

Renders snitch events to a video. Provides options to adjust render look and feel, events included, duration, quality, etc.

Examples:

* `.render`
* `.render --past 1d`
* `.render --past 1w1d2h --user gregy165`
* `.render --start 07/18/2022` (end and start default to current time if not specified)
* `.render --start 07/18/2022 --end 07/29/2022`
* `.render --size 1200 --duration 30 --fps 30` (high quality, long render)

#### Arguments

| Argument          | Description
| ----------------  | ------------------------------------------------------------------------------------------------------------------------
| -a/--all-snitches | If passed, all known snitches will be rendered, not just the snitches pinged by the relevant events. Warning: this can result in very small or unreadable event fields. |
| -s/--size           | The resolution of the render, in pixels. Defaults to 700. Decrease if you want faster renders, increase if you want higher quality renders. |
| -f/--fps      | The frames per second of the render. Defaults to 20. Decrease if you want faster renders, increase if you want smoother renders. |
| -d/--duration     | The duration of the render, in seconds. If you want to take a slower, more careful look at events, specify a higher value. If you just want a quick glance, specify a lower value. Higher values take longer to render. |
| -u/--users   | If passed, only events by these users will be rendered. |
| -p/--past           | How far in the past to look for events. Specify in human-readable form, ie -p 1y2mo5w2d3h5m2s ("1 year 2 months 5 weeks 2 days 3 hours 5 minutes 2 seconds ago"), or any combination thereof, ie -p 1h30m ("1 hour 30 minutes ago"). Use the special value "all" to visualize all events. |
| --start     | The start date of events to include. Use the format `mm/dd/yyyy` or `mm/dd/yy`, eg 7/18/2022 or 12/31/21. If --start is passed but not --end, *all* events after the passed start date will be rendered. |
| --end  |  The end date of events to include. Use the format `mm/dd/yyyy` or `mm/dd/yy`, eg 7/18/2022 or 12/31/21. If --end is passed but not --start, *all* events before the passed end date will be rendered. |
| --fade            | What percentage of the video duration event highlighting will be visible for. At --fade 100, every event will remain on screen for the entire render. At --fade 50, events will remain on screen for half the render. Fade duration is limited to a minimum of 1.5 seconds regardless of what you specify for --fade. Defaults to 10% of video duration (equivalent to --fade 10). |
| -l/--line      | Draw lines between snitch events instead of the default boxes around individual snitch events. This option is experimental and may not look good. It is intended to provide an easier way to see directionality and travel patterns than the default mode, and may eventually become the default mode. |

### Tutorial

Walks you through an initial setup of snitchvis.

Example: `.tutorial`

Takes no arguments.

### Channel Add

Adds a snitch channel(es), viewable by the specified roles.

Example: `.channel add #snitches-citizens #snitches-border -r citizens`

#### Positional Arguments

| Name | Description |
| ---      | ---         |
| channels | The channels to add. Use a proper channel mention (eg #snitches) to specify a channel. |

#### Arguments

| Argument | Description |
| ---      | ---         |
| -r/--roles | The roles which will be able to render events from this channel. Use the name of the role (don't ping the role). Use the name `everyone` to grant all users access to render the snitches. |

### Channel List

Lists the current snitch channels and what roles can view them.

Example: `.channel list`

Takes no arguments.

### Channel Remove

Removes a snitch channel(es) from the list of snitch channels.

Example: `.channel remove #snitches-citizens`

#### Positional Arguments

| Name | Description |
| ---      | ---         |
| channels | The channels to remove. Use a proper channel mention (eg #snitches) to specify a channel. |

### Events

Lists the most recent events for the specified snitch or snitches.

Example: `.events --name "shop entrance"`

### Arguments

| Argument | Description |
| ---      | ---         |
| -n/--name | List events for snitches with the specified name. |
| -l/--location | List events for snitches at this location. Format is `-l/--location x y z` or `-l/--location x z`. The two parameter version is a convenience to avoid having to specify a y level; snitches at all y levels at that (x, z) location will be searched for events. |

### Full Reindex

Drops all currently indexed snitches and re-indexes from scratch. This can help with some rare issues. You probably don't want to do this unless you know what you're doing, or have been advised to do so by tybug.

Example: `.full-reindex`

#### Arguments

| Argument | Description |
| ---      | ---         |
| -y | Pass to confirm you would like to reindex the server. |

### Import Snitches

Imports snitches from a SnitchMod database.

You will likely have to use this command multiple times on the same database if you have a tiered hierarchy of snitch groups; for instance, you might run `.import-snitches -g mta-citizens mta-shops -r citizen` to import snitches citizens can render, and then `.import-snitches -g mta-cabinet -r cabinet` to import snitches only cabinet members can render.

Example: `.import-snitches -g mta-citizens mta-shops -r citizen` (include a snitchvis database file upload in the same message)

#### Arguments

| Argument | Description |
| ---      | ---         |
| -g/--groups | Only snitches in the database which are reinforced to one of these groups will be imported. If you really want to import all snitches in the database, pass `-g all`. |
| -r/--roles | Users with at least one of these roles will be able to render the imported snitches. Use the name of the role (don't ping the role). Use the name `everyone` to grant all users access to the snitches. |

### Index

Indexes messages in the current snitch channels.

Example: `.index`

Takes no arguments.

### Permissions

Lists what snitch channels you have have permission to render events from. This is based on your discord roles and how you set up the snitch channels (see `.channel list`).

Example: `.permissions`

Takes no arguments.

### Help

Displays available commands.

Example: `.help`

Takes no arguments.


## Setup Guide

```
git clone https://github.com/tybug/snitchvisbot
cd snitchvisbot
pip install -r requirements.txt
touch secret.py
```

Open `secret.py` and add your discord bot token:

```python
TOKEN = "LONG_STRING"
```

Now:

```
python main.py
```

Database is created on first run. There's currently no way to deal with database migrations.

### Headless

If you're running on a headless server, or somewhere without an X server, you'll need to wrap the call in a virtual X server:

```
sudo apt install xvfb
xvfb-run python main.py
```

SnitchVis doesn't actually use an X server to render, but we do need to trick Qt into thinking an X server is available, or it will complain and crash.

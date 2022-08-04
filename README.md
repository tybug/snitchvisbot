# SnitchVisBot

A discord bot for visualizing snitch events. Uses [SnitchVis](https://github.com/tybug/snitchvis) to render the videos.


## Commands

| Command          | Description                                                                                                                    |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| .tutorial        | Walks you through an initial setup of snitchvis.                                                                               |
| .render          | Renders snitch events to a video. Provides options to adjust render look and feel, events included, duration, quality, etc.    |
| .channel add     | Adds a snitch channel(es), viewable by the specified roles.                                                                    |
| .channel list    | Lists the current snitch channels and what roles can view them.                                                                |
| .channel remove  | Removes a snitch channel(es) from the list of snitch channels.                                                                 |
| .events          | Lists the most recent events for the specified snitch or snitches.                                                             |
| .full-reindex    | Drops all currently indexed snitches and re-indexes from scratch.                                                              |
| .import-snitches | Imports snitches from a SnitchMod database.                                                                                    |
| .index           | Indexes messages in the current snitch channels.                                                                               |
| .permissions     | Lists what snitch channels you have permission to render events from.                                                          |
| .help            | Displays available commands.                                                                                                   |


## Render Options


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

## Other Command Options

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

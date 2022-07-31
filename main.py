from datetime import datetime
from tempfile import NamedTemporaryFile, TemporaryDirectory
import sqlite3
from pathlib import Path
from asyncio import Queue

from discord import File
from snitchvis import (Event, InvalidEventException, SnitchVisRecord,
    create_users, snitches_from_events, Snitch)
from PyQt6.QtWidgets import QApplication

import db
import utils
from secret import TOKEN
from command import command, Arg, channel, role, human_timedelta, human_datetime
from client import Client

INVITE_URL = ("https://discord.com/oauth2/authorize?client_id="
    "999808708131426434&permissions=0&scope=bot")
LOG_CHANNEL = 1002607241586823270
PREFIX = "."

class Snitchvis(Client):
    def __init__(self, *args, **kwargs):
        super().__init__(PREFIX, LOG_CHANNEL, *args, **kwargs)
        # there's a potential race condition when indexing messages on startup,
        # where we spend x seconds indexing channels before some channel c,
        # but than at y < x seconds a new message comes in to channel c which
        # gets processed and causes the last indexed id to be set to a very high
        # id, causing us not to index the messages we didn't see while we were
        # down when self.index_channel gets called on c.
        # To prevent this, we'll stop indexing new messages at all while
        # indexing channels on startup, and instead stick the new messages into
        # a queue. This queue will be processed in the order the messages were
        # received once we're done indexing the channels and can be assured we
        # won't mess up our last_indexed_id.
        self.defer_indexing = False
        self.indexing_queue = Queue()

        # we can only have one qapp active at a time, but we want to be able to
        # be rendering multiple snitch logs at the same time (ie multiple .v
        # commands, potentially in different servers). We'll keep a master qapp
        # active at the top level, but never exec it, which is enough to let us
        # draw on qimages and generate videos with SnitchVisRecord and
        # FrameRenderer.
        # https://stackoverflow.com/q/13215120 for platform/minimal args
        self.qapp = QApplication(['-platform', 'minimal'])

    async def on_ready(self):
        await super().on_ready()
        print("connected to discord")
        # avoid last_indexed_id getting set to a wrong value by incoming
        # messages while we index channels
        self.defer_indexing = True
        # index any messages sent while we were down
        for channel in db.get_snitch_channels(None):
            c = self.get_channel(channel.id)
            permissions = c.permissions_for(c.guild.me)
            if not permissions.read_messages:
                print(f"Couldn't index {c} / {c.id} (guild {c.guild} / "
                    f"{c.guild.id}) without read_messages permission")
                continue
            await self.index_channel(channel, c)
        db.commit()

        # index messages in the order we received them now that it's safe to do
        # so. New messages might get added to the queue while we're in the
        # middle of processing these, so it's important to continuously poll the
        # queue.
        while not self.indexing_queue.empty():
            message = await self.indexing_queue.get()
            await self.maybe_index_message(message)

        # now that we've indexed the channels and fully processed the queue, we
        # can go back to indexing new messages normally.
        self.defer_indexing = False

    async def on_message(self, message):
        await super().on_message(message)
        if not self.defer_indexing:
            await self.maybe_index_message(message)
        else:
            self.indexing_queue.put_nowait(message)

    async def maybe_index_message(self, message):
        snitch_channel = db.get_snitch_channel(message.channel)
        # only index messages in snitch channels which have been fully indexed
        # by `.index` already. If someone adds a snitch channel with
        # `.channel add #snitches`, and then a snitch ping is immediately sent
        # in that channel, we don't want to update the last indexed id (or
        # index the message at all) until the channel has been fully indexed
        # manually.
        if not snitch_channel or not snitch_channel.last_indexed_id:
            return

        try:
            event = Event.parse(message.content)
        except InvalidEventException:
            return

        db.add_event(message, event)
        db.update_last_indexed(message.channel, message.id)

    async def index_channel(self, channel, discord_channel):
        print(f"Indexing channel {discord_channel} / {discord_channel.id}, "
            f"guild {discord_channel.guild} / {discord_channel.guild.id}")
        events = []
        last_id = channel.last_indexed_id
        async for message_ in discord_channel.history(limit=None):
            # don't index past the last indexed message id (if we have such
            # an id stored)
            if last_id and message_.id <= last_id:
                break

            try:
                event = Event.parse(message_.content)
            except InvalidEventException:
                continue
            events.append([message_, event])

        last_messages = await discord_channel.history(limit=1).flatten()
        # only update if the channel has messages
        if last_messages:
            last_message = last_messages[0]
            db.update_last_indexed(channel, last_message.id, commit=False)

        for (message_, event) in events:
            # caller is responsible for committing
            db.add_event(message_, event, commit=False)

        return events

    @command("setup",
        help="Helps you with initial setup of snitchvis.",
        permissions=["manage_guild"]
    )
    async def setup(self, message):
        await message.channel.send("todo")


    @command("channel add",
        args=[
            Arg("channels", nargs="+", convert=channel, help="The "
                "channels to add. Use a proper channel mention "
                "(eg #snitches) to specify a channel."),
            Arg("-r", "--roles", nargs="+", convert=role, help="The roles "
                "which will be able to render events from this channel. Use the "
                "name of the role (don't ping the role). Use the name "
                "`everyone` to grant all users access to the snitches.")
        ],
        help="Add a snitch channel, viewable by the specified roles.",
        permissions=["manage_guild"]
    )
    async def channel_add(self, message, channels, roles):
        for channel in channels:
            if db.snitch_channel_exists(channel):
                await message.channel.send(f"{channel.mention} is already a "
                    "snitch channel. If you would like to change which roles "
                    f"have access to {channel.mention}, first remove it "
                    "(`.channel remove`) then re-add it (`.channel add`) with "
                    "the desired roles.")
                return
            db.add_snitch_channel(channel, roles)

        await message.channel.send(f"Added {utils.channel_str(channels)} to "
            f"snitch channels.")


    @command("channel remove",
        args=[
            Arg("channels", nargs="+", convert=channel, help="The "
                "channels to remove. Use a proper channel mention "
                "(eg #snitches) to specify a channel.")
        ],
        help="Removes a snitch channel, or multiple channels, from the list of "
            "snitch channels.",
        permissions=["manage_guild"]
    )
    async def channel_remove(self, message, channels):
        for channel in channels:
            db.remove_snitch_channel(channel)

        await message.channel.send(f"Removed {utils.channel_str(channels)} "
            "from snitch channels.")


    @command("channel list",
        help="Lists the current snitch channels and what roles can view them.",
        permissions=["manage_guild"]
    )
    async def channel_list(self, message):
        channels = db.get_snitch_channels(message.guild)
        if not channels:
            await message.channel.send("No snitch channels set. You can add "
                "snitch channels with `.channel add`.")
            return

        m = "Current snitch channels:\n"
        for channel in channels:
            m += f"\n{utils.channel_accessible(message.guild, channel)}"
        await message.channel.send(m)


    @command("index",
        help="Indexes messages in the current snitch channels.",
        permissions=["manage_guild"]
    )
    async def index(self, message):
        channels = db.get_snitch_channels(message.guild)

        if not channels:
            await message.channel.send("No snitch channels to index. Use "
                "`.channel add #channel` to add snitch channels.")
            return

        await message.channel.send("Indexing the following snitch channels: "
            f"{utils.channel_str(channels)}. This could take a LONG time if "
            "they have lots of messages in them.")

        for channel in channels:
            # make sure we can read all the snitch channels
            c = channel.to_discord(message.guild)
            permissions = c.permissions_for(message.guild.me)
            if not permissions.read_messages:
                await message.channel.send("Snitchvis doesn't have permission "
                    f"to read messages in {channel.mention}. Either give "
                    "snitchvis enough permissions to read messages there, or "
                    "remove it from the list of snitch channels (with "
                    "`.channel remove`).")
                return

        for channel in channels:
            await message.channel.send(f"Indexing {channel.mention}...")
            c = channel.to_discord(message.guild)
            events = await self.index_channel(channel, c)
            db.commit()

            await message.channel.send(f"Added {len(events)} new events from "
                f"{channel.mention}")

        await message.channel.send("Finished indexing snitch channels")

    @command("full-reindex",
        args=[
            Arg("-y", store_boolean=True, help="Pass to confirm you would like "
            "to reindex the server.")
        ],
        help="Fully reindexes this server. This command drops all currently "
            "indexed snitches and will re-index from scratch. This can help "
            "with some rare issues. You probably don't want to do this unless "
            "you know what you're doing, or have been advised to do so by "
            "tybug.",
        permissions=["manage_guild"]
    )
    async def full_reindex(self, message, y):
        if not y:
            await message.channel.send("This command will delete all currently "
                "indexed snitches and will re-index from scratch. This can "
                "help with some rare issues. You probably don't want to do "
                "this unless you know what you're doing, or have been advised "
                "to do so by tybug. If you're sure you would like to reindex, "
                "run `.full-reindex -y`.")
            return
        await message.channel.send("Dropping all events and resetting last "
            "indexed ids")
        # drop all events
        db.execute("DELETE FROM event WHERE guild_id = ?", [message.guild.id])
        # reset last indexed id so indexing works from scratch again
        db.execute("UPDATE snitch_channel SET last_indexed_id = null "
            "WHERE guild_id = ?", [message.guild.id])
        # finally, reindex.
        await self.index(message)

    @command("v",
        # TODO make defaults for these parameters configurable
        args=[
            Arg("-a", "--all-snitches", default=False, store_boolean=True,
                help="If passed, all known snitches will be rendered, not "
                "just the snitches pinged by the relevant events. Warning: "
                "this can result in very small or unreadable event fields."),
            Arg("-s", "--size", default=800, convert=int, help="The resolution "
                "of the render, in pixels. Defaults to 800. Decrease if "
                "you want faster renders, increase if you want higher quality "
                "renders."),
            Arg("-f", "--fps", default=30, convert=int, help="The frames per "
                "second of the render. Defaults to 30. Decrease if you want "
                "faster renders, increase if you want smoother renders."),
            Arg("-d", "--duration", default=5, convert=int, help="The duration "
                "of the render, in seconds. If you want to take a slower, more "
                "careful look at events, specify a higher value. If you just "
                "want a quick glance, specify a lower value. Higher values "
                "take longer to render."),
            Arg("-u", "--users", nargs="*", default=[], help="If passed, only "
                "events by these users will be rendered."),
            Arg("-p", "--past", convert=human_timedelta, help="How far in the "
                "past to look for events. Specify in human-readable form, ie "
                "-p 1y2mo5w2d3h5m2s (\"1 year 2 months 5 weeks 2 days 3 hours 5 "
                "minutes 2 seconds ago\"), or any combination thereof, ie "
                "-p 1h30m (\"1 hour 30 minutes ago\")."),
            Arg("--start", convert=human_datetime),
            Arg("--end", convert=human_datetime),
            Arg("--fade", default=10, convert=float),
            Arg("-l", "--line", store_boolean=True)
        ],
        help="Visualizes (renders) snitch events. Provides options to adjust "
            "render look and feel, events included, duration, quality, etc."
    )
    async def visualize(self, message, all_snitches, size, fps, duration, users,
        past, start, end, fade, line
    ):
        NO_EVENTS = ("No events match those criteria. Try adding snitch "
            "channels with `.channel add #channel`, indexing with `.index`, or "
            "adjusting your parameters to include more snitch events.")

        if past:
            end = datetime.utcnow().timestamp()
            if past == "all":
                # conveniently, start of epoch is 0 ms
                start = 0
            else:
                start = end - past.total_seconds()
        else:
            if not start and not end:
                # neither set
                # slightly special behavior: instead of going back in the past
                # `x` ms, go back to the most recent event (however long ago
                # that may be) and *then* go back `x` ms and grab all those
                # events.
                event = db.most_recent_event(message.guild)
                # if the guild doesn't have any events at all yet, complain and
                # exit.
                if not event:
                    await message.channel.send(NO_EVENTS)
                    return
                end = event.t.timestamp()
                # TODO make adjustable instead of hardcoding 30 minutes, not
                # sure what parameter name to use though (--past-adjusted?)
                start = end - (30 * 60)
            elif start and not end:
                # only start set. Set end to current date
                start = start.timestamp()
                end = datetime.utcnow().timestamp()
            elif end and not start:
                # only end set. Set start to beginning of time
                start = 0
                end = end.timestamp()
            else:
                # both set
                start = start.timestamp()
                end = end.timestamp()

        if end < start:
            await message.channel.send("End date can't be before start date.")
            return

        # TODO warn if no events by the specified users are in the events filter
        events = db.get_events(message.guild, message.author, start, end, users)

        if not events:
            await message.channel.send(NO_EVENTS)
            return

        event_mode = "line" if line else "square"
        all_events = db.get_all_events(message.guild)
        # use all known events to construct snitches
        snitches = snitches_from_events(all_events)
        # if the guild has any snitches uploaded (via .import-snitches), use
        # those as well, even if they've never been pinged.
        # Only retrieve snitches which the author has access to via their roles
        snitches |= set(db.get_snitches(message.guild, message.author.roles))
        users = create_users(events)
        # duration to ms
        duration *= 1000

        with TemporaryDirectory() as d:
            output_file = str(Path(d) / "out.mp4")

            def run_snitch_vis():
                vis = SnitchVisRecord(snitches, events, users, size, fps,
                    duration, all_snitches, fade, event_mode, output_file)
                vis.render()

            m = await message.channel.send("rendering video...")
            await self.loop.run_in_executor(None, run_snitch_vis)
            vis_file = File(output_file)
            await message.channel.send(file=vis_file)
            await m.delete()

    @command("import-snitches",
        args=[
            Arg("-g", "--groups", nargs="+", help="Only snitches in the "
                "database which are reinforced to one of these groups will be "
                "imported. If you really want to import all snitches in the "
                "database, pass `-g all`."),
            Arg("-r", "--roles", nargs="+", convert=role, help="Users with at "
                "least one of these roles will be able to render the "
                "imported snitches. Use the name of the role (don't ping the "
                "role). Use the name `everyone` to grant all users access to "
                "the snitches.")
        ],
        help=("Imports snitches from a a SnitchMod database. When importing, "
            "specify a list of namelayer groups to filter the snitches by, and "
            "a list of discord roles which will be able to access those "
            "snitches. Also upload a snitches.sqlite file in the same message."
            "\n"
            "You will likely have to use this command multiple times on the "
            "same database if you have a tiered hierarchy of snitch groups; "
            "for instance, you might run `.import-snitches -g mta-citizens "
            "mta-shops -r citizen` to import snitches citizens can render, "
            "and then `.import-snitches -g mta-cabinet -r cabinet` to import "
            "snitches only cabinet members can render."),
        permissions=["manage_guild"]
    )
    async def import_snitches(self, message, groups, roles):
        attachments = message.attachments
        if not attachments:
            await message.channel.send("You must upload a `snitch.sqlite` file "
                "in the same message as the `.import-snitches` command.")
            return

        with NamedTemporaryFile() as f:
            attachment = attachments[0]
            await attachment.save(f.name)
            conn = sqlite3.connect(f.name)
            cur = conn.cursor()

            for group in groups:
                if group == "all":
                    continue
                row = cur.execute("SELECT COUNT(*) FROM snitches_v2 WHERE "
                    "group_name = ?", [group]).fetchone()
                if row[0] == 0:
                    await message.channel.send("No snitches on namelayer "
                        f"group `{group}` found in this database. If the "
                        "group name is correct, omit it and re-run to "
                        "avoid this error.")
                    await message.channel.send("Import aborted. You may "
                        "safely re-run this import with different "
                        "parameters.")
                    return

            await message.channel.send("Importing snitches from snitchmod "
                "database...")

            snitches_added = 0
            if any(group == "all" for group in groups):
                group_filter = "1"
            else:
                group_filter = f"group_name IN ({('?, ' * len(groups))[:-2]}"

            rows = cur.execute("SELECT * FROM snitches_v2 WHERE "
                f"{group_filter}").fetchall()

            for row in rows:
                snitch = Snitch.from_snitchmod(row)
                # batch commit for speed
                rowcount = db.add_snitch(message.guild, snitch, roles,
                    commit=False)
                snitches_added += rowcount

            db.commit()

        await message.channel.send(f"Added {snitches_added} new snitches.")

    @command("permissions", help="Lists what snitch channels you have "
        "have permission to render events from. This is based on your discord "
        "roles and how you set up the snitch channels (see `.channel list`).")
    async def permissions(self, message):
        # tells the command author what snitch channels they can view.
        snitch_channels = db.get_snitch_channels(message.guild)

        channels = set()
        for role in message.author.roles:
            for channel in snitch_channels:
                if role.id in channel.allowed_roles:
                    channels.add(channel)

        if not channels:
            await message.channel.send("You can't render any events.")
            return

        await message.channel.send("You can render events from the "
            f"following channels: {utils.channel_str(channels)}")

    @command("events", help="Lists the most recent events for the specified "
        "snitch or snitches.",
        args=[
            Arg("-n", "--name", help="List events for snitches with the "
                "specified name."),
            Arg("-l", "--location", help="List events for snitches at this "
                "location. Format is `-l/--location x y z` or "
                "`-l/--location x z`. The two parameter version is a "
                "convenience to avoid having to specify a y level; snitches at "
                "all y levels at that (x, z) location will be searched for "
                "events.", nargs="*")
        ]
    )
    async def events(self, message, name, location):
        if not bool(name) ^ bool(location):
            await message.channel.send("Exactly one of `-n/--name` or "
                "`-l/--location` must be passed.\nRun `.events --help` for "
                "more information.")
            return

        if name:
            events = db.select("""
                SELECT * FROM event
                WHERE guild_id = ? AND snitch_name = ?
                LIMIT 10
            """, [message.guild.id, name])
        elif location:
            if len(location) == 2:
                x, z = location
                y = None
            elif len(location) == 3:
                x, y, z = location
            else:
                await message.channel.send(f"Invalid location "
                    f"`{' '.join(location)}`. Must be in the form "
                    "`-l/--location x y z` or `-l/--location x z`")
                return

            try:
                x = int(x)
            except ValueError:
                await message.channel.send(f"Invalid x coordinate `{x}`")
                return

            try:
                y = int(y) if y else None
            except ValueError:
                await message.channel.send(f"Invalid y cooridnate `{y}`")
                return

            try:
                z = int(z)
            except ValueError:
                await message.channel.send(f"Invalid z coordinate `{z}`")
                return

            # swap y and z because that's what the db expects
            if y is not None:
                events = db.select("""
                    SELECT * FROM event
                    WHERE guild_id = ? AND x = ? AND y = ? AND z = ?
                    LIMIT 10
                """, [message.guild.id, x, z, y])
            else:
                events = db.select("""
                    SELECT * FROM event
                    WHERE guild_id = ? AND x = ? AND y = ?
                    LIMIT 10
                """, [message.guild.id, x, z])

        if not events:
            await message.channel.send("No events match those criteria.")
            return

        messages = []
        for event in events:
            t = datetime.fromtimestamp(event["t"]).strftime('%Y-%m-%d %H:%M:%S')
            group = event["namelayer_group"]
            username = event["username"]
            snitch_name = event["snitch_name"]
            x = event["x"]
            y = event["y"]
            z = event["z"]

            messages.append(f"`[{t}]` `[{group}]` **{username}** is at "
                f"{snitch_name} ({x},{z},{y})")
        await message.channel.send("10 most recent events matching those "
            "criteria:\n" + "\n".join(messages))

client = Snitchvis()
client.run(TOKEN)

# TODO make lines mode in visualizer actually worth using - highlight single
# events, distinguish actual events and the lines, add arrows to indicate
# directionality

# TODO add "centered at (x, y)" coordinates to info text, can be confusing where
# the vis is sometimes

# TODO support custom kira message formats

# TODO help output

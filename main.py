from datetime import datetime
from tempfile import NamedTemporaryFile, TemporaryDirectory
import sqlite3
from pathlib import Path

from discord import File
from snitchvis import (Event, InvalidEventException, SnitchVisRecord,
    create_users, snitches_from_events, Snitch)
from PyQt6.QtWidgets import QApplication

from asyncio import Queue

import db
import utils
from secret import TOKEN
from command import command, Arg, channel, role, human_timedelta
from client import Client

INVITE_URL = ("https://discord.com/oauth2/authorize?client_id="
    "999808708131426434&permissions=0&scope=bot")

class Snitchvis(Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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
        # avoid last_indexed_id getting set to a wrong value by incoming
        # messages while we index channels
        self.defer_indexing = True
        # index any messages sent while we were down
        for channel in db.get_snitch_channels(None):
            c = self.get_channel(channel.id)
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

    @command("setup")
    async def setup(self, message):
        await message.channel.send("todo")


    @command("channel add",
        args=[
            Arg("channels", nargs="+", convert=channel),
            Arg("-r", "--roles", nargs="+", convert=role)
        ]
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
            Arg("channels", nargs="+", convert=channel)
        ]
    )
    async def channel_remove(self, message, channels):
        for channel in channels:
            db.remove_snitch_channel(channel)

        await message.channel.send(f"Removed {utils.channel_str(channels)} "
            "from snitch channels.")


    @command("channel list")
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


    @command("index")
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
                    "snitchvis enough permissions to read messages in "
                    f"{channel.mention}, or remove it from the list of snitch "
                    "channels (with `.channel remove`).")
                return

        for channel in channels:
            await message.channel.send(f"Indexing {channel.mention}...")
            c = channel.to_discord(message.guild)
            events = await self.index_channel(channel, c)
            db.commit()

            await message.channel.send(f"Added {len(events)} new events from "
                f"{channel.mention}")

        await message.channel.send("Finished indexing snitch channels")

    @command("full-reindex", args=[Arg("-y", store_boolean=True)])
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
            Arg("-a", "--all-snitches", default=False, store_boolean=True),
            Arg("-s", "--size", default=500, convert=int),
            Arg("-f", "--fps", default=20, convert=int),
            Arg("-d", "--duration", default=5, convert=int),
            Arg("-u", "--users", nargs="*", default=[]),
            Arg("-p", "--past", convert=human_timedelta),
            Arg("--start"),
            Arg("--end"),
            Arg("--fade", default=10, convert=float)
        ]
    )
    async def visualize(self, message, all_snitches, size, fps, duration, users,
        past, start, end, fade
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
                start = end - past
        else:
            if start and end:
                # TODO
                pass
            else:
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

        # TODO warn if no events by the specified users are in the events filter
        events = db.get_events(message.guild, message.author, start, end, users)

        if not events:
            await message.channel.send(NO_EVENTS)
            return

        all_events = db.get_all_events(message.guild)
        # use all known events to construct snitches
        snitches = snitches_from_events(all_events)
        # if the guild has any snitches uploaded (via .import-snitches), use
        # those as well, even if they've never been pinged
        snitches |= set(db.get_snitches(message.guild))
        users = create_users(events)
        # duration to ms
        duration *= 1000

        with TemporaryDirectory() as d:
            output_file = str(Path(d) / "out.mp4")

            def run_snitch_vis():
                vis = SnitchVisRecord(snitches, events, users, size, fps,
                    duration, all_snitches, fade, output_file)
                vis.render()

            m = await message.channel.send("rendering video...")
            # TODO does this incur an overhead compared to running it syncly?
            # probably not, but worth a check.
            await self.loop.run_in_executor(None, run_snitch_vis)
            vis_file = File(output_file)
            await message.channel.send(file=vis_file)
            await m.delete()

    @command("import-snitches")
    async def import_snitches(self, message):
        attachments = message.attachments
        if not attachments:
            await message.channel.send("You must upload a snitch.sqlite file "
                "as part of the `.import-snitches` command")
            return

        await message.channel.send("Importing snitches from snitchmod "
            "database...")

        with NamedTemporaryFile() as f:
            attachment = attachments[0]
            await attachment.save(f.name)
            conn = sqlite3.connect(f.name)
            cur = conn.cursor()
            rows = cur.execute("SELECT * FROM snitches_v2").fetchall()

            snitches_added = 0
            for row in rows:
                snitch = Snitch.from_snitchmod(row)
                # batch commit for speed
                cur = db.add_snitch(message.guild, snitch, commit=False)
                snitches_added += cur.rowcount

            db.commit()

        await message.channel.send(f"Added {snitches_added} new snitches.")

    @command("permissions")
    async def permissions(self, message):
        # tells the command author what snitch channels they can view.
        snitch_channels = db.get_snitch_channels(message.guild)

        channels = set()
        for role in message.author.roles:
            for channel in snitch_channels:
                if role.id in channel.allowed_roles:
                    channels.add(channel)

        await message.channel.send("You can visualize events from the "
            f"following channels: {utils.channel_str(channels)}")

client = Snitchvis()
client.run(TOKEN)

# TODO "lines" mode in visualizer which draws colored lines between events
# instead of highlighted boxes (what to do about single events? probably just a
# colored square, ie a line between itself and itself) and breaks up events by a
# time period, ~1hr. Also draws arrows on the lines to indicate directionality.

# TODO add "centered at (x, y)" coordinates to info text, can be confusing where
# the vis is sometimes

# TODO support custom kira message formats

from datetime import datetime
from tempfile import NamedTemporaryFile
import sqlite3

from discord import File
from snitchvis import (Event, InvalidEventException, SnitchVisRecord,
    create_users, snitches_from_events, Snitch)

import db
import utils
from secret import TOKEN
from command import command, Arg, channel, role, human_timedelta
from client import Client

INVITE_URL = ("https://discord.com/oauth2/authorize?client_id="
    "999808708131426434&permissions=0&scope=bot")

class Snitchvis(Client):
    async def on_message(self, message):
        await super().on_message(message)
        await self.maybe_index_message(message)

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
            await message.channel.send(f"Indexing {channel.mention}...")

            events = []
            # convert to an actual channel object so we can retrieve history
            c = channel.to_discord(message.guild)
            last_id = channel.last_indexed_id
            async for message_ in c.history(limit=None):
                # don't index past the last indexed message id (if we have such
                # an id stored)
                if last_id and message_.id <= last_id:
                    break

                try:
                    event = Event.parse(message_.content)
                except InvalidEventException:
                    continue
                events.append([message_, event])

            last_messages = await c.history(limit=1).flatten()
            # only update if the channel has messages
            if last_messages:
                last_message = last_messages[0]
                db.update_last_indexed(channel, last_message.id)

            for (message_, event) in events:
                # batch commit for speed
                db.add_event(message_, event, commit=False)
            db.commit()

            await message.channel.send(f"Added {len(events)} new events from "
                f"{channel.mention}")

        await message.channel.send("Finished indexing snitch channels")


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

        snitch_channels = db.get_snitch_channels(message.guild)
        channel_ids = []
        for channel in snitch_channels:
            channel = channel.to_discord(message.guild)
            permissions = channel.permissions_for(message.author)
            # only retrieve events for channels this user has access to
            if not permissions.read_messages:
                continue

            channel_ids.append(channel.id)

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
        output_file = "out.mp4"

        def run_snitch_vis():
            vis = SnitchVisRecord(snitches, events, users, size, fps,
                duration, all_snitches, fade, output_file)
            vis.exec()

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

client = Snitchvis()
client.run(TOKEN)

# TODO reindex known snitch channels on bot restart, careful to immediately
# retrieve last_indexed_id so it doesn't get updated by an incoming message and
# cause us to lose all messages between the last indexed message and the
# incoming message (messages might have come in while the bot was down that are
# waiting to be indexed).

# TODO "lines" mode in visualizer which draws colored lines between events
# instead of highlighted boxes (what to do about single events? probably just a
# colored square, ie a line between itself and itself) and breaks up events by a
# time period, ~1hr. Also draws arrows on the lines to indicate directionality.

# TODO add "centered at (x, y)" coordinates to info text, can be confusing where
# the vis is sometimes

# TODO support custom kira message formats

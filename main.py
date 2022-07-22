from discord import Client
from discord.utils import remove_markdown
from snitchvis import Event, InvalidEventException

from secret import TOKEN

INVITE_URL = ("https://discord.com/oauth2/authorize?client_id="
    "999808708131426434&permissions=0&scope=bot")

class MyClient(Client):
    async def on_message(self, message):
        if message.content == ".setup":
            await self.setup(message)

    async def setup(self, message):
        await message.channel.send("looking for snitch channels...")
        snitch_channels = set()

        for channel in message.guild.text_channels:
            async for message in channel.history(limit=5):
                # remove backticks and bold formatting to avoid confusing our
                # event parser
                content = remove_markdown(message.content)
                try:
                    Event.parse(content)
                except InvalidEventException:
                    continue

                snitch_channels.add(channel)

        if not snitch_channels:
            await message.channel.send("Couldn't find any snitch channels. "
                "Make sure Snitchvis can see the snitch channels you want it "
                "to have access to, and can read the message history of those "
                "channels.")
            return

        channel_str = ""
        for channel in snitch_channels:
            channel_str += channel.mention

        await message.channel.send("found the following snitch channels: "
            f"{channel_str}")


client = MyClient()
client.run(TOKEN)

# TODO reindexing known snitch channels (automatically / on command)

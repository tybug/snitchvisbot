from dataclasses import dataclass

@dataclass
class SnitchChannel:
    guild_id: int
    # discord channel id
    id: int
    last_indexed_message_id: int

    # make compatible with utils.channel_str
    @property
    def mention(self):
        return f"<#{self.id}>"

    def to_discord(self, guild):
        return guild.get_channel(self.id)

@dataclass
class Event:
    message_id: int
    channel_id: int
    guild_id: int

    username: str
    snitch_name: str
    namelayer_group: str
    x: int
    y: int
    z: int
    # time in ms
    t: int

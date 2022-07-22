from dataclasses import dataclass
from datetime import datetime

@dataclass
class SnitchChannel:
    guild_id: int
    # discord channel id
    id: int
    last_indexed_id: int

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
    # gets passed as ms since epoch and converted on post_init
    t: datetime

    def __post_init__(self):
        self.t = datetime.fromtimestamp(self.t)

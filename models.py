from dataclasses import dataclass
from datetime import datetime

@dataclass
class SnitchChannel:
    guild_id: int
    # discord channel id
    id: int
    last_indexed_id: int
    allowed_roles: list[int]

    # make compatible with utils.channel_str
    @property
    def mention(self):
        return f"<#{self.id}>"

    def to_discord(self, guild):
        return guild.get_channel(self.id)

    def allowed_roles_to_discord(self, guild):
        return [guild.get_role(role) for role in self.allowed_roles]

    def __hash__(self):
        return hash((self.id))

    def __eq__(self, other):
        return self.id == other.id

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

@dataclass
class Snitch:
    guild_id: int
    world: str
    x: int
    y: int
    z: int
    group_name: str
    type: str
    name: str
    dormant_ts: int
    cull_ts: int
    first_seen_ts: int
    last_seen_ts: int
    created_ts: int
    created_by_uuid: str
    renamed_ts: int
    renamed_by_uuid: str
    lost_jalist_access_ts: int
    broken_ts: int
    gone_ts: int
    tags: str
    notes: str

    def __hash__(self):
        return hash((self.world, self.x, self.y, self.z))

    def __eq__(self, other):
        return (self.x == other.x and self.y == other.y and self.z == other.z
            and self.world == other.world)

@dataclass
class LivemapChannel:
    guild_id: int
    channel_id: int
    last_message_id: int

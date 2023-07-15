from dataclasses import dataclass
from datetime import datetime, timezone

from discord import User, Guild
from discord.abc import Messageable

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
        self.t = datetime.fromtimestamp(self.t, timezone.utc)

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

@dataclass
class LivemapLogChannel:
    guild_id: int
    livemap_channel_id: int
    log_channel_id: int

@dataclass
class Command:
    guild_id: int
    command: str
    command_text: str

    # make compliant with actual Command class
    @property
    def name(self):
        return self.command
    @property
    def use_prefix(self):
        return True

@dataclass
class KiraConfig:
    guild_id: int
    snitch_format: str
    snitch_enter_message: str
    snitch_login_message: str
    snitch_logout_message: str
    time_format: str

# used when we want to fake a discord message with our own
# user/channel/guild/content.
@dataclass
class FakeMessage:
    author: User
    channel: Messageable
    guild: Guild
    content: str

# subclass Messageable for correct typing.
class ForwardingChannel(Messageable):
    def __init__(self, messageable, *, log_prefix, forward_to):
        self.__messageable = messageable
        self.__log_prefix = log_prefix
        self.__forward_to = forward_to

    def __getattr__(self, val):
        # then transparently forward any accesses to our authenticated object.
        return getattr(self.__messageable, val)

    async def send(self, content=None, *args, file=None, **kwargs):
        # this is a bit of a song and dance to achieve the following behavior:
        # * if an attachment is sent with the message, don't upload it to our
        #   forwarding channel. Ideally, we would like to forward attachments as
        #   well, but discord.File objects are single-use and aren't easily
        #   copyable.
        # * if an attachment is sent with no content, forward a placeholder
        #   <attachment> message, since we can't forward the attachment and also
        #   can't send empty messages. Alternative is not forwarding messages
        #   with attachments at all.
        ret = await self.__messageable.send(content, *args, file=file, **kwargs)

        if not content and file:
            content = "<attachment>"

        log_message = f"{self.__log_prefix} [response]\n{content}"
        await self.__forward_to.send(log_message, *args, **kwargs)
        return ret

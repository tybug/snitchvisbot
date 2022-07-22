from dataclasses import dataclass

@dataclass
class SnitchChannel:
    guild_id: int
    channel_id: int

    # make compatible with utils.channel_str
    @property
    def mention(self):
        return f"<#{self.channel_id}>"

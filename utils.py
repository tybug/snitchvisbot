import dateparser
from datetime import timezone

def channel_str(channels):
    return ", ".join(channel.mention for channel in channels)

def role_str(roles):
    return "`" + "`, `".join(role.name for role in roles) + "`"

def channel_accessible(guild, channel):
    roles = channel.allowed_roles_to_discord(guild)
    roles_str = role_str(roles)
    return f"{channel.mention} (accessible by {roles_str})"

def try_dateparser(val):
    from command import ParseError
    settings = {
        "TIMEZONE": "utc"
    }

    datetime = dateparser.parse(val, settings=settings)
    if datetime is None:
        raise ParseError(f"Invalid time `{val}`.")

    return datetime.replace(tzinfo=timezone.utc)

def message_log_prefix(guild, author):
    return f"[`{guild.name}`] [{author.mention} / `{author.name}`]"


# https://gist.github.com/kkrypt0nn/a02506f3712ff2d1c8ca7c9e0aed7c06
class ANSI:
    BLACK = "\u001b[0;30m"
    RED = "\u001b[0;31m"
    GREEN = "\u001b[0;32m"
    BROWN = "\u001b[0;33m"
    BLUE = "\u001b[0;34m"
    PURPLE = "\u001b[0;35m"
    CYAN = "\u001b[0;36m"
    WHITE = "\u001b[0;37m"

    # I don't think these names are right. I think [1; means bold for discord
    # not, "light".
    DARK_GREY = "\u001b[1;30m"
    LIGHT_RED = "\u001b[1;31m"
    LIGHT_GREEN = "\u001b[1;32m"
    YELLOW = "\u001b[1;33m"
    LIGHT_BLUE = "\u001b[1;34m"
    LIGHT_PURPLE = "\u001b[1;35m"
    LIGHT_CYAN = "\u001b[1;36m"
    LIGHT_WHITE = "\u001b[1;37m"

    BOLD = "\u001b[1m"
    FAINT = "\u001b[2m"
    ITALIC = "\u001b[3m"
    UNDERLINE = "\u001b[4m"
    BLINK = "\u001b[5m"
    NEGATIVE = "\u001b[7m"
    CROSSED = "\u001b[9m"
    END = "\u001b[0m"

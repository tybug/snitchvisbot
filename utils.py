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

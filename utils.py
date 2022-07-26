def channel_str(channels):
    return ", ".join(channel.mention for channel in channels)

def role_str(roles):
    return "`" + "`, `".join(role.name for role in roles) + "`"

def channel_accessible(guild, channel):
    roles = channel.allowed_roles_to_discord(guild)
    roles_str = role_str(roles)
    return f"{channel.mention} (accessible by {roles_str})"

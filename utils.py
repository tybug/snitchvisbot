def channel_str(channels):
    return ", ".join(channel.mention for channel in channels)

def snitch_channels_message(channels):
    if not channels:
        return "No snitch channels set"
    return f"Current snitch channels: {channel_str(channels)}"

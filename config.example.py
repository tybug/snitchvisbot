import os

TOKEN = os.environ.get('TOKEN')
TESTING = os.environ.get('TESTING', True)
TESTING_GUILDS = [os.environ.get('TESTING_GUILD', "")]
AUTHOR_ID = os.environ.get('AUTHOR_ID')
COMMAND_LOG_CHANNEL = os.environ.get('COMMAND_LOG_CHANNEL')
JOIN_LOG_CHANNEL = os.environ.get('JOIN_LOG_CHANNEL')
ERROR_LOG_CHANNEL = os.environ.get('ERROR_LOG_CHANNEL')
LIVEMAP_LOG_CATEGORY = os.environ.get('LIVEMAP_LOG_CATEGORY')
DEFAULT_PREFIX = os.environ.get('DEFAULT_PREFIX', ".")
KIRA_ID = os.environ.get('KIRA_ID', 952325487663939645) 

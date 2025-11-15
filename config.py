import os
import logging
from dotenv import load_dotenv

load_dotenv()

class ConfigValidationError(Exception):
    pass

class Config(object):
    LOG_FORMAT = os.getenv("LOG_FORMAT") or '%(asctime)s - %(levelname)s - %(message)s \t - %(name)s (%(filename)s).%(funcName)s(%(lineno)d) '
    LOG_LEVEL = os.getenv("LOG_LEVEL") or 'INFO'
    APPNAME = os.getenv("APPNAME") or 'Truth Social Monitor'
    ENV = os.getenv("ENV") or "DEV"
    REPEAT_DELAY = int(os.getenv("REPEAT_DELAY") or 300)  # 5 minutes default

    # Discord configuration
    DISCORD_NOTIFY = os.getenv("DISCORD_NOTIFY", 'True').lower() == 'true'
    DISCORD_USERNAME = os.getenv("DISCORD_USERNAME") or "Truth Social Bot"
    DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
    
    # Supabase configuration
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")
    SUPABASE_TABLE = os.getenv("SUPABASE_TABLE") or "posts"

    # Truth Social configuration
    TRUTH_USERNAME = os.getenv("TRUTH_USERNAME")
    TRUTH_INSTANCE = os.getenv("TRUTH_INSTANCE") or "truthsocial.com"
    POST_TYPE = os.getenv("POST_TYPE") or "post"  # Default to "post" if not specified
    
    # Request configuration
    REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT") or 30)
    MAX_RETRIES = int(os.getenv("MAX_RETRIES") or 3)

    # FlareSolverr configuration
    FLARESOLVERR_ADDRESS = os.getenv("FLARESOLVERR_ADDRESS") or "localhost"
    FLARESOLVERR_PORT = int(os.getenv("FLARESOLVERR_PORT") or 8191)


    def __init__(self):
        self.validate_config()

    def validate_config(self):
        """Validate required configuration settings"""
        errors = []

        if not self.TRUTH_USERNAME:
            errors.append("TRUTH_USERNAME is required")

        if self.DISCORD_NOTIFY:
            if not self.DISCORD_WEBHOOK_URL:
                errors.append("DISCORD_WEBHOOK_URL is required when DISCORD_NOTIFY is enabled")

        if not self.SUPABASE_URL:
            errors.append("SUPABASE_URL is required")
        if not self.SUPABASE_KEY:
            errors.append("SUPABASE_KEY is required")

        if errors:
            raise ConfigValidationError("\n".join(errors))

        return True

"""
Configuration module for Fantasy Baseball Assistant.
Loads environment variables with validation.
"""

import os
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Yahoo OAuth settings (can be placeholder for testing)
    YAHOO_CLIENT_ID: str = "placeholder_client_id"
    YAHOO_CLIENT_SECRET: str = "placeholder_client_secret"
    YAHOO_REDIRECT_URI: str = "http://localhost:8000/callback"
    
    # Yahoo OAuth endpoints
    YAHOO_AUTH_URL: str = "https://api.login.yahoo.com/oauth2/request_auth"
    YAHOO_TOKEN_URL: str = "https://api.login.yahoo.com/oauth2/get_token"
    YAHOO_API_BASE: str = "https://fantasysports.yahooapis.com/fantasy/v2"
    YAHOO_OAUTH_SCOPE: Optional[str] = None
    
    # Database settings
    DATABASE_URL: str = "sqlite:///./data/fantasy_assistant.db"
    
    # Server settings
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    SSL_CERTFILE: Optional[str] = None
    SSL_KEYFILE: Optional[str] = None

    # Session secret for cookie signing
    SESSION_SECRET: str = "change-this-in-production"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
    
    def validate(self) -> bool:
        """Validate that required settings are properly configured."""
        # Only fail if credentials are completely empty or still placeholders
        # Check for the placeholder text that indicates unconfigured state
        placeholder_ids = ["your_client_id_here", "placeholder_client_id", ""]
        placeholder_secrets = ["your_client_secret_here", "placeholder_client_secret", ""]
        
        if self.YAHOO_CLIENT_ID in placeholder_ids:
            raise ValueError("YAHOO_CLIENT_ID is not configured. Please edit .env file.")
        if self.YAHOO_CLIENT_SECRET in placeholder_secrets:
            raise ValueError("YAHOO_CLIENT_SECRET is not configured. Please edit .env file.")
        return True


# Global settings instance
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get or create the global settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.validate()
    return _settings


def reload_settings() -> Settings:
    """Force reload of settings (useful for testing)."""
    global _settings
    _settings = Settings()
    _settings.validate()
    return _settings
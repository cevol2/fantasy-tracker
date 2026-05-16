"""
Yahoo OAuth module for Fantasy Baseball Assistant.
Handles OAuth 2.0 authorization-code flow with Yahoo.
"""

import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User, create_user, get_user_by_guid, update_user_token

logger = logging.getLogger(__name__)


class YahooOAuthError(Exception):
    """Exception raised for Yahoo OAuth errors."""
    pass


class YahooOAuth:
    """
    Yahoo OAuth 2.0 handler.
    Implements the authorization-code flow for Yahoo Fantasy Sports API.
    """
    
    def __init__(self):
        self.settings = get_settings()
        self.client_id = self.settings.YAHOO_CLIENT_ID
        self.client_secret = self.settings.YAHOO_CLIENT_SECRET
        self.redirect_uri = self.settings.YAHOO_REDIRECT_URI
        self.auth_url = self.settings.YAHOO_AUTH_URL
        self.token_url = self.settings.YAHOO_TOKEN_URL
    
    def get_authorization_url(self, state: Optional[str] = None) -> Tuple[str, str]:
        """
        Generate the Yahoo authorization URL.
        
        Returns:
            Tuple of (authorization_url, state)
        """
        if state is None:
            state = secrets.token_urlsafe(32)
        
        # Yahoo OAuth parameters
        # Note: Yahoo uses 'client_id' not 'consumer_key'
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "state": state
        }
        if self.settings.YAHOO_OAUTH_SCOPE:
            params["scope"] = self.settings.YAHOO_OAUTH_SCOPE
        
        auth_url = f"{self.auth_url}?{urlencode(params)}"
        logger.info(f"Generated authorization URL with state: {state[:8]}...")
        
        return auth_url, state
    
    def exchange_code_for_tokens(self, code: str) -> dict:
        """
        Exchange authorization code for access and refresh tokens.
        
        Args:
            code: The authorization code from Yahoo callback
            
        Returns:
            Dict containing access_token, refresh_token, expires_in, etc.
        """
        logger.info("Exchanging authorization code for tokens")
        
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "code": code,
            "grant_type": "authorization_code"
        }
        
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    self.token_url,
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"}
                )
            
            if response.status_code != 200:
                logger.error(f"Token exchange failed with status {response.status_code}")
                raise YahooOAuthError(f"Token exchange failed: {response.text}")
            
            token_data = response.json()
            logger.info("Successfully exchanged code for tokens")
            
            return token_data
            
        except httpx.HTTPError as e:
            logger.error(f"HTTP error during token exchange: {e}")
            raise YahooOAuthError(f"Failed to exchange code: {str(e)}")
    
    def refresh_access_token(self, refresh_token: str) -> dict:
        """
        Refresh an expired access token using the refresh token.
        
        Args:
            refresh_token: The refresh token from previous authentication
            
        Returns:
            Dict containing new access_token, refresh_token, expires_in, etc.
        """
        logger.info("Refreshing access token")
        
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token"
        }
        
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    self.token_url,
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"}
                )
            
            if response.status_code != 200:
                logger.error(f"Token refresh failed with status {response.status_code}")
                raise YahooOAuthError(f"Token refresh failed: {response.text}")
            
            token_data = response.json()
            logger.info("Successfully refreshed access token")
            
            return token_data
            
        except httpx.HTTPError as e:
            logger.error(f"HTTP error during token refresh: {e}")
            raise YahooOAuthError(f"Failed to refresh token: {str(e)}")
    
    def calculate_expiry(self, expires_in: int) -> datetime:
        """
        Calculate token expiry datetime.
        
        Args:
            expires_in: Token lifetime in seconds
            
        Returns:
            Datetime when token expires (with 5-minute buffer)
        """
        # Subtract 5 minutes for safety buffer
        buffer_seconds = 300
        return datetime.utcnow() + timedelta(seconds=max(expires_in - buffer_seconds, 60))
    
    def get_or_create_user(self, db: Session, code: str) -> User:
        """
        Complete OAuth flow and get/create user.
        
        Args:
            db: Database session
            code: Authorization code from Yahoo
            
        Returns:
            User object with valid tokens
        """
        # Exchange code for tokens
        token_data = self.exchange_code_for_tokens(code)
        
        # Extract token info
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in", 3600)
        yahoo_guid = token_data.get("xoauth_yahoo_guid") or token_data.get("guid")
        
        if not access_token or not refresh_token:
            raise YahooOAuthError("Invalid token response from Yahoo")
        
        # Fetch Yahoo GUID from token response, or fallback to userinfo endpoints
        if not yahoo_guid:
            yahoo_guid = self._get_yahoo_guid(access_token)
        
        # Calculate expiry
        token_expiry = self.calculate_expiry(expires_in)
        
        # Check if user exists
        user = get_user_by_guid(db, yahoo_guid)
        
        if user:
            # Update existing user's tokens
            user = update_user_token(db, user, access_token, refresh_token, token_expiry)
            logger.info(f"Updated tokens for existing user: {yahoo_guid}")
        else:
            # Create new user
            user = create_user(db, yahoo_guid, access_token, refresh_token, token_expiry)
            logger.info(f"Created new user from OAuth: {yahoo_guid}")
        
        return user
    
    def refresh_user_token(self, db: Session, user: User) -> User:
        """
        Refresh a user's access token.
        
        Args:
            db: Database session
            user: User whose token needs refresh
            
        Returns:
            Updated User object with new tokens
        """
        if not user.refresh_token:
            raise YahooOAuthError("No refresh token available")
        
        token_data = self.refresh_access_token(user.refresh_token)
        
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in", 3600)
        
        if not access_token:
            raise YahooOAuthError("Invalid refresh token response")
        
        token_expiry = self.calculate_expiry(expires_in)
        
        user = update_user_token(db, user, access_token, refresh_token, token_expiry)
        logger.info(f"Refreshed tokens for user: {user.yahoo_guid}")
        
        return user
    
    def _get_yahoo_guid(self, access_token: str) -> str:
        """
        Get the Yahoo GUID for the authenticated user.
        
        Args:
            access_token: Valid access token
            
        Returns:
            Yahoo GUID string
        """
        logger.info("Fetching Yahoo user GUID")
        
        # Try multiple endpoints to get the GUID
        endpoints = [
            "https://api.login.yahoo.com/openid/v1/userinfo",
            "https://api.login.yahoo.com/oauth2/v1/userinfo",
            "https://fantasysports.yahooapis.com/fantasy/v2/users;use_login=1?format=json"
        ]
        
        try:
            with httpx.Client(timeout=30.0) as client:
                for url in endpoints:
                    logger.info(f"Trying endpoint: {url}")
                    response = client.get(
                        url,
                        headers={
                            "Authorization": f"Bearer {access_token}",
                            "Accept": "application/json"
                        }
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        logger.info(f"Response from {url}: {str(data)[:200]}")
                        
                        # Check for GUID in headers first
                        user_guid = response.headers.get("x-yahoo-user-guid")
                        
                        if not user_guid and isinstance(data, dict):
                            if "sub" in data:
                                user_guid = data["sub"]
                            elif "guid" in data:
                                user_guid = data["guid"]
                            elif "user" in data and isinstance(data["user"], dict):
                                user_guid = data["user"].get("guid")
                            elif "users" in data:
                                users = data["users"].get("user")
                                if isinstance(users, dict):
                                    user_guid = users.get("guid") or users.get("id")
                                elif isinstance(users, list) and users:
                                    first_user = users[0]
                                    if isinstance(first_user, dict):
                                        user_guid = first_user.get("guid") or first_user.get("id")
                            elif "fantasy_content" in data:
                                content = data["fantasy_content"]
                                users = content.get("users") or content.get("user")
                                if isinstance(users, dict):
                                    user = users.get("user")
                                    if isinstance(user, list) and user:
                                        user = user[0]
                                    if isinstance(user, dict):
                                        user_guid = user.get("guid") or user.get("id")
                                    else:
                                        # Handle numeric keys under users
                                        for user_block in users.values():
                                            if isinstance(user_block, dict):
                                                user_entry = user_block.get("user")
                                                if isinstance(user_entry, list) and user_entry:
                                                    first_user = user_entry[0]
                                                    if isinstance(first_user, dict):
                                                        user_guid = first_user.get("guid") or first_user.get("id")
                                                        if user_guid:
                                                            break
                        
                        if user_guid:
                            logger.info(f"Retrieved Yahoo GUID: {user_guid[:8]}...")
                            return user_guid
            
            # If we get here, GUID extraction failed
            raise YahooOAuthError("Could not extract Yahoo GUID from any known endpoint")
            
        except httpx.HTTPError as e:
            logger.error(f"HTTP error fetching Yahoo GUID: {e}")
            raise YahooOAuthError(f"Failed to get Yahoo GUID: {str(e)}")


# Global OAuth instance
_oauth: Optional[YahooOAuth] = None


def get_yahoo_oauth() -> YahooOAuth:
    """Get or create the global Yahoo OAuth instance."""
    global _oauth
    if _oauth is None:
        _oauth = YahooOAuth()
    return _oauth
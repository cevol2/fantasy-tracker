"""
Yahoo API client for Fantasy Baseball Assistant.
Handles all Fantasy Sports API calls to Yahoo.
"""

import logging
from datetime import datetime
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    User, League, Team, Roster, Standings,
    upsert_league, upsert_team, save_roster, save_standings,
    get_leagues_by_user, get_teams_by_user, get_latest_roster, get_latest_standings
)
from app.yahoo_oauth import YahooOAuthError, get_yahoo_oauth

logger = logging.getLogger(__name__)


class YahooAPIError(Exception):
    """Exception raised for Yahoo API errors."""
    pass


class YahooFantasyClient:
    """
    Yahoo Fantasy Sports API client.
    Handles all API calls to Yahoo Fantasy Sports endpoints.
    """
    
    def __init__(self, user: User, db: Session):
        """
        Initialize the Yahoo Fantasy client.
        
        Args:
            user: Authenticated user with valid tokens
            db: Database session
        """
        self.user = user
        self.db = db
        self.settings = get_settings()
        self.oauth = get_yahoo_oauth()
        self.base_url = self.settings.YAHOO_API_BASE.rstrip("/")
        
        # Ensure valid access token
        self._ensure_valid_token()
    
    def _ensure_valid_token(self) -> None:
        """Refresh token if expired."""
        if self.user.is_token_expired():
            logger.info(f"Token expired for user {self.user.yahoo_guid[:8]}..., refreshing")
            self.user = self.oauth.refresh_user_token(self.db, self.user)
            logger.info("Token refreshed successfully")
    
    def _make_request(self, method: str, url: str, **kwargs) -> dict:
        """
        Make an authenticated request to Yahoo API.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            url: Full URL to request
            **kwargs: Additional arguments for httpx
            
        Returns:
            Parsed JSON response
        """
        self._ensure_valid_token()
        
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.user.access_token}"
        headers["Accept"] = "application/json"
        
        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.request(method, url, headers=headers, **kwargs)
            
            if response.status_code == 401:
                # Token might have been revoked, try refreshing
                logger.warning("Received 401, attempting token refresh")
                self.user = self.oauth.refresh_user_token(self.db, self.user)
                headers["Authorization"] = f"Bearer {self.user.access_token}"
                
                with httpx.Client(timeout=60.0) as client:
                    response = client.request(method, url, headers=headers, **kwargs)
            
            if response.status_code != 200:
                logger.error(f"API request failed: {response.status_code} - {response.text[:200]}")
                raise YahooAPIError(f"API request failed: {response.text[:500]}")
            
            logger.debug(f"API request successful: {url}")
            return response.json()
            
        except httpx.HTTPError as e:
            logger.error(f"HTTP error during API request: {e}")
            raise YahooAPIError(f"API request failed: {str(e)}")
    
    def get_games(self) -> list[dict]:
        """
        Get user's fantasy games.
        Note: Yahoo API uses game_keys parameter to filter games.
        
        Returns:
            List of game objects
        """
        logger.info("Fetching Yahoo games")
        
        # Yahoo API endpoint for user games
        # game_keys=mlb filters to Major League Baseball
        # use_login=1 ensures we get the logged-in user's data
        url = f"{self.base_url}/users;use_login=1/games"
        params = {"game_keys": "mlb", "format": "json"}
        
        url_with_params = f"{url}?game_keys=mlb&format=json"
        data = self._make_request("GET", url_with_params)
        
        # Parse games from Yahoo response
        games = []
        if "fantasy_content" in data:
            fc = data["fantasy_content"]
            users = fc.get("users") or fc.get("user")
            if isinstance(users, dict):
                user = users.get("user")
                if isinstance(user, list):
                    user = user[0]
                if user and "games" in user:
                    game_list = user["games"].get("game")
                    if isinstance(game_list, dict):
                        game_list = [game_list]
                    games = game_list if isinstance(game_list, list) else []
        elif "games" in data and "game" in data["games"]:
            game_list = data["games"]["game"]
            if isinstance(game_list, dict):
                game_list = [game_list]
            games = game_list if isinstance(game_list, list) else []
        
        logger.info(f"Found {len(games)} games")
        return games
    
    def get_leagues(self) -> list[dict]:
        """
        Get user's fantasy baseball leagues.
        Note: This endpoint returns league info, not teams within leagues.
        
        Returns:
            List of league objects
        """
        logger.info("Fetching Yahoo leagues")
        
        # Yahoo API endpoint for user's leagues
        # game_keys=mlb specifies Major League Baseball
        url = f"{self.base_url}/users;use_login=1/games"
        url_with_params = f"{url}?game_keys=mlb&format=json"
        
        data = self._make_request("GET", url_with_params)
        
        # Parse leagues from Yahoo response
        leagues = []
        if "fantasy_content" in data:
            fc = data["fantasy_content"]
            users = fc.get("users") or fc.get("user")
            if isinstance(users, dict):
                user = users.get("user")
                if isinstance(user, list):
                    user = user[0]
                if user and "games" in user:
                    game = user["games"].get("game")
                    if isinstance(game, dict) and "leagues" in game:
                        league_data = game["leagues"]
                        if "league" in league_data:
                            league_list = league_data["league"]
                            leagues = league_list if isinstance(league_list, list) else [league_list]
        elif "games" in data and "game" in data["games"]:
            game = data["games"]["game"]
            if isinstance(game, dict) and "leagues" in game:
                league_data = game["leagues"]
                if "league" in league_data:
                    league_list = league_data["league"]
                    leagues = league_list if isinstance(league_list, list) else [league_list]
        
        logger.info(f"Found {len(leagues)} leagues")
        return leagues
    
    def get_league_metadata(self, league_key: str) -> dict:
        """
        Get detailed metadata for a specific league.
        
        Args:
            league_key: Yahoo league key (e.g., "394.l.12345")
            
        Returns:
            League metadata dict
        """
        logger.info(f"Fetching league metadata for: {league_key}")
        
        # Yahoo API endpoint for league metadata
        url = f"{self.base_url}/game/{league_key.split('.')[0]}/league/{league_key}?format=json"
        data = self._make_request("GET", url)
        
        league_meta = {}
        if "fantasy_content" in data and "league" in data["fantasy_content"]:
            league_meta = data["fantasy_content"]["league"]
        
        logger.info(f"Retrieved metadata for league: {league_meta.get('name', 'Unknown')}")
        return league_meta
    
    def get_league_standings(self, league_key: str) -> list[dict]:
        """
        Get league standings.
        
        Args:
            league_key: Yahoo league key
            
        Returns:
            List of team standings
        """
        logger.info(f"Fetching league standings for: {league_key}")
        
        # Yahoo API endpoint for league standings
        url = f"{self.base_url}/game/{league_key.split('.')[0]}/league/{league_key}/standings?format=json"
        data = self._make_request("GET", url)
        
        standings = []
        if "fantasy_content" in data and "league" in data["fantasy_content"]:
            league_data = data["fantasy_content"]["league"]
            if "standings" in league_data and "team" in league_data["standings"]:
                team_list = league_data["standings"]["team"]
                standings = team_list if isinstance(team_list, list) else [team_list]
        
        logger.info(f"Retrieved {len(standings)} standings entries")
        return standings
    
    def get_team_metadata(self, team_key: str) -> dict:
        """
        Get detailed metadata for a specific team.
        
        Args:
            team_key: Yahoo team key (e.g., "394.l.12345.t.1")
            
        Returns:
            Team metadata dict
        """
        logger.info(f"Fetching team metadata for: {team_key}")
        
        # Yahoo API endpoint for team metadata
        url = f"{self.base_url}/game/{team_key.split('.')[0]}/team/{team_key}?format=json"
        data = self._make_request("GET", url)
        
        team_meta = {}
        if "fantasy_content" in data and "team" in data["fantasy_content"]:
            team_meta = data["fantasy_content"]["team"]
        
        logger.info(f"Retrieved metadata for team: {team_meta.get('name', 'Unknown')}")
        return team_meta
    
    def get_team_roster(self, team_key: str, week: Optional[int] = None) -> dict:
        """
        Get team roster.
        
        Args:
            team_key: Yahoo team key
            week: Optional week number (defaults to current week)
            
        Returns:
            Roster data dict
        """
        league_key = team_key.rsplit('.t.', 1)[0]
        week_str = str(week) if week else "0"  # 0 means current week
        
        logger.info(f"Fetching roster for team: {team_key}, week: {week_str}")
        
        # Yahoo API endpoint for team roster
        # Note: week=0 returns current week's roster
        url = f"{self.base_url}/game/{team_key.split('.')[0]}/team/{team_key}/roster?format=json"
        params = f"?week={week_str}"
        data = self._make_request("GET", url + params)
        
        roster = {}
        if "fantasy_content" in data and "team" in data["fantasy_content"]:
            team_data = data["fantasy_content"]["team"]
            if "roster" in team_data:
                roster = team_data["roster"]
        
        logger.info(f"Retrieved roster with entries")
        return roster
    
    def sync_leagues(self) -> list[League]:
        """
        Sync all user's leagues from Yahoo and store in database.
        
        Returns:
            List of synced League objects
        """
        logger.info("Syncing leagues from Yahoo")
        
        # Fetch leagues from Yahoo
        yahoo_leagues = self.get_leagues()
        synced_leagues = []
        
        for league_data in yahoo_leagues:
            try:
                league_key = league_data.get("league_key") or league_data.get("key")
                name = league_data.get("name", "Unknown League")
                game_key = league_data.get("game_key") or league_key.split('.')[0]
                
                # Extract additional info if available
                num_teams = league_data.get("num_teams", 0)
                current_week = league_data.get("current_week", 0)
                start_week = league_data.get("start_week", 0)
                end_week = league_data.get("end_week", 0)
                
                league = upsert_league(
                    self.db,
                    user_id=self.user.id,
                    league_key=league_key,
                    name=name,
                    game_key=game_key,
                    season=2024,  # Yahoo provides this in URL, not response
                    league_type="full",
                    num_teams=num_teams,
                    current_week=current_week,
                    start_week=start_week,
                    end_week=end_week,
                    league_data=league_data
                )
                synced_leagues.append(league)
                
            except Exception as e:
                logger.error(f"Error syncing league {league_data}: {e}")
        
        logger.info(f"Successfully synced {len(synced_leagues)} leagues")
        return synced_leagues
    
    def sync_team(self, team_key: str) -> Team:
        """
        Sync a specific team from Yahoo.
        
        Args:
            team_key: Yahoo team key
            
        Returns:
            Synced Team object
        """
        logger.info(f"Syncing team: {team_key}")
        
        # Get team metadata
        team_data = self.get_team_metadata(team_key)
        
        # Extract team info
        name = team_data.get("name", "Unknown Team")
        league_key = team_key.rsplit('.t.', 1)[0]
        
        # Get or create league
        league = self.db.query(League).filter(League.league_key == league_key).first()
        if not league:
            league = upsert_league(
                self.db,
                user_id=self.user.id,
                league_key=league_key,
                name=league_key,
                game_key=league_key.split('.')[0],
                season=2024
            )
        
        # Upsert team
        team = upsert_team(
            self.db,
            user_id=self.user.id,
            league_id=league.id,
            team_key=team_key,
            name=name,
            team_data=team_data
        )
        
        logger.info(f"Synced team: {name}")
        return team
    
    def sync_roster(self, team: Team, week: Optional[int] = None) -> Roster:
        """
        Sync team roster from Yahoo.
        
        Args:
            team: Team object
            week: Optional week number
            
        Returns:
            Saved Roster object
        """
        logger.info(f"Syncing roster for team: {team.team_key}")
        
        # Get roster from Yahoo
        roster_data = self.get_team_roster(team.team_key, week)
        
        # Determine week
        if week is None:
            week = 0  # Current week
        
        # Save roster
        roster = save_roster(self.db, team.id, week, roster_data)
        
        logger.info(f"Saved roster with {len(roster_data.get('players', {}).get('player', []))} players")
        return roster
    
    def sync_standings(self, league: League) -> Standings:
        """
        Sync league standings from Yahoo.
        
        Args:
            league: League object
            
        Returns:
            Saved Standings object
        """
        logger.info(f"Syncing standings for league: {league.league_key}")
        
        # Get standings from Yahoo
        standings_data = self.get_league_standings(league.league_key)
        
        # Save standings
        standings = save_standings(self.db, league.id, standings_data)
        
        logger.info(f"Saved standings for {len(standings_data)} teams")
        return standings


def get_user_leagues(db: Session, user_id: int) -> list[dict]:
    """Get all leagues for a user with their Yahoo data."""
    leagues = get_leagues_by_user(db, user_id)
    return [league.to_dict() for league in leagues]


def get_user_teams(db: Session, user_id: int) -> list[dict]:
    """Get all teams for a user."""
    teams = get_teams_by_user(db, user_id)
    return [team.to_dict() for team in teams]
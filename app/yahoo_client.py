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


def _extract_metadata(item: Any) -> dict:
    """
    Yahoo returns items as either a plain dict or a list [metadata, subresources].
    This extracts just the metadata (index 0 of list, or the dict itself).
    """
    if isinstance(item, list):
        return item[0] if len(item) > 0 and isinstance(item[0], dict) else {}
    return item if isinstance(item, dict) else {}


def _get_subresources(item: Any) -> dict:
    """
    Yahoo returns items as either a plain dict or a list [metadata, subresources].
    This extracts the sub-resources dict (index 1 of list).
    """
    if isinstance(item, list) and len(item) > 1 and isinstance(item[1], dict):
        return item[1]
    return {}


def _extract_items_from_container(container: Any, outer_key: str) -> list[tuple[dict, dict]]:
    """
    Extract items from Yahoo's numeric-keyed container.
    Returns list of (metadata, subresources) tuples.
    
    Yahoo format: {0: {outer_key: [metadata, {subresources}]}, 1: {...}}
    """
    results = []
    if not isinstance(container, dict):
        return results
    for k in sorted(container.keys(), key=lambda x: (not str(x).isdigit(), int(x) if str(x).isdigit() else x)):
        entry = container[k]
        if isinstance(entry, dict) and outer_key in entry:
            item = entry[outer_key]
            metadata = _extract_metadata(item)
            sub = _get_subresources(item)
            results.append((metadata, sub))
    return results


class YahooFantasyClient:
    """
    Yahoo Fantasy Sports API client.
    Handles all API calls to Yahoo Fantasy Sports endpoints.
    """
    
    def __init__(self, user: User, db: Session):
        self.user = user
        self.db = db
        self.settings = get_settings()
        self.oauth = get_yahoo_oauth()
        self.base_url = self.settings.YAHOO_API_BASE.rstrip("/")
        self._ensure_valid_token()
    
    def _ensure_valid_token(self) -> None:
        if self.user.is_token_expired():
            logger.info(f"Token expired for user {self.user.yahoo_guid[:8]}..., refreshing")
            self.user = self.oauth.refresh_user_token(self.db, self.user)
            logger.info("Token refreshed successfully")
    
    def _make_request(self, method: str, url: str, **kwargs) -> dict:
        self._ensure_valid_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.user.access_token}"
        headers["Accept"] = "application/json"
        
        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.request(method, url, headers=headers, **kwargs)
            if response.status_code == 401:
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
    
    def get_games(self) -> list[tuple[dict, dict]]:
        """
        Get user's fantasy games.
        Returns list of (metadata, sub_resources) tuples.
        """
        logger.info("Fetching Yahoo games")
        url = f"{self.base_url}/users;use_login=1/games?format=json"
        data = self._make_request("GET", url)
        
        games = []
        try:
            fc = data.get("fantasy_content", {})
            users_container = fc.get("users", fc)
            # users: {0: {user: [metadata, {games: {...}}]}}
            for user_meta, user_sub in _extract_items_from_container(users_container, "user"):
                games_container = user_sub.get("games", {})
                games = _extract_items_from_container(games_container, "game")
        except Exception as e:
            logger.warning(f"Error parsing games: {e}")
        
        logger.info(f"Found {len(games)} games")
        return games
    
    def get_leagues(self) -> list[dict]:
        """
        Get user's active fantasy baseball leagues.
        Only fetches leagues for active, non-offseason MLB games.
        """
        logger.info("Discovering active MLB game keys")
        games = self.get_games()
        
        # Find the most recent active MLB game
        target_game_key = None
        for game_meta, _ in games:
            code = game_meta.get("code", "")
            is_game_over = int(game_meta.get("is_game_over", 1))
            is_offseason = int(game_meta.get("is_offseason", 1))
            game_key = game_meta.get("game_key", "")
            
            if code == "mlb" and not is_game_over and not is_offseason:
                target_game_key = game_key
                logger.info(f"Found active MLB game: key={game_key}, season={game_meta.get('season')}")
                break
        
        if not target_game_key:
            logger.warning("No active MLB game found")
            return []
        
        # Fetch leagues for this active game
        try:
            url = f"{self.base_url}/users;use_login=1/games;game_keys={target_game_key}/leagues?format=json"
            data = self._make_request("GET", url)
            
            fc = data.get("fantasy_content", {})
            users_container = fc.get("users", fc)
            leagues = []
            for user_meta, user_sub in _extract_items_from_container(users_container, "user"):
                games_container = user_sub.get("games", {})
                for game_meta, game_sub in _extract_items_from_container(games_container, "game"):
                    leagues_container = game_sub.get("leagues", {})
                    for league_meta, league_sub in _extract_items_from_container(leagues_container, "league"):
                        leagues.append(league_meta)
            
            logger.info(f"Found {len(leagues)} leagues for game {target_game_key}")
            return leagues
            
        except Exception as e:
            logger.error(f"Error fetching leagues: {e}")
            return []
    
    def get_league_metadata(self, league_key: str) -> dict:
        logger.info(f"Fetching league metadata for: {league_key}")
        url = f"{self.base_url}/league/{league_key}?format=json"
        data = self._make_request("GET", url)
        
        league_meta = {}
        try:
            fc = data.get("fantasy_content", {})
            league_container = fc.get("league", {})
            leagues = _extract_items_from_container(league_container, "league")
            if leagues:
                league_meta = leagues[0][0]
        except Exception as e:
            logger.warning(f"Error parsing league metadata: {e}")
        
        logger.info(f"Retrieved metadata for league: {league_meta.get('name', 'Unknown')}")
        return league_meta
    
    def get_league_standings(self, league_key: str) -> list[dict]:
        """
        Get league standings.
        
        Yahoo response format:
        fantasy_content.league = [
          {league_metadata},                                    # [0]
          {standings: [{teams: {"0": {team: [props, stats]}, ...}}]}  # [1]
        ]
        Each team is an array: [props_array, {team_stats: ...}]
        props_array contains dicts like {name: "..."}, {team_key: "..."}, etc.
        """
        logger.info(f"Fetching league standings for: {league_key}")
        url = f"{self.base_url}/league/{league_key}/standings?format=json"
        data = self._make_request("GET", url)
        
        standings = []
        try:
            fc = data.get("fantasy_content", {})
            league_arr = fc.get("league", [])
            if not isinstance(league_arr, list) or len(league_arr) < 2:
                logger.warning(f"Unexpected league format: {type(league_arr)}")
                return []
            
            # league_arr[1] = {standings: [{teams: {...}}]}
            standings_block = league_arr[1].get("standings") if isinstance(league_arr[1], dict) else None
            if not isinstance(standings_block, list) or len(standings_block) < 1:
                return []
            
            # standings_block[0] = {teams: {0: {team: [...]}, 1: {...}}}
            teams_container = standings_block[0].get("teams") if isinstance(standings_block[0], dict) else None
            if not isinstance(teams_container, dict):
                return []
            
            # teams has numeric keys: {0: {team: [props, stats]}, 1: {...}}
            for k in sorted(teams_container.keys(), key=lambda x: (not str(x).isdigit(), int(x) if str(x).isdigit() else x)):
                team_entry = teams_container[k]
                if not isinstance(team_entry, dict):
                    continue
                team_arr = team_entry.get("team", [])
                if not isinstance(team_arr, list) or len(team_arr) < 1:
                    continue
                
                # team[0] = props_array which contains dicts like {name: ...}, {team_key: ...}
                props_arr = team_arr[0] if isinstance(team_arr[0], list) else []
                
                # Build team dict from props array elements
                team_data = {}
                for prop in props_arr:
                    if isinstance(prop, dict):
                        team_data.update(prop)
                
                # Merge ALL remaining elements from the team array (not just index 1)
                # Yahoo response varies by league type:
                #   index 1: {team_stats: ...} (present in all leagues)
                #   index 2: {team_points: ...} (points leagues)
                #   index 2/3: {team_standings: ...} (H2H leagues, contains outcome_totals)
                #   index 3/4: {team_remaining_games: ...} etc.
                for i in range(1, len(team_arr)):
                    if isinstance(team_arr[i], dict):
                        team_data.update(team_arr[i])
                
                standings.append(team_data)
                
        except Exception as e:
            logger.warning(f"Error parsing standings: {e}")
        
        logger.info(f"Retrieved {len(standings)} standings entries")
        return standings
    
    def get_team_metadata(self, team_key: str) -> dict:
        logger.info(f"Fetching team metadata for: {team_key}")
        url = f"{self.base_url}/team/{team_key}?format=json"
        data = self._make_request("GET", url)
        
        team_meta = {}
        try:
            fc = data.get("fantasy_content", {})
            team_arr = fc.get("team", [])
            # Yahoo team endpoint returns: [props_array, {sub_resources}]
            # props_array is a list of single-key dicts like [{name: {full: ...}}, {team_key: ...}, ...]
            if isinstance(team_arr, list) and len(team_arr) > 0:
                props_arr = team_arr[0]
                if isinstance(props_arr, list):
                    for prop in props_arr:
                        if isinstance(prop, dict):
                            team_meta.update(prop)
                elif isinstance(props_arr, dict):
                    team_meta.update(props_arr)
        except Exception as e:
            logger.warning(f"Error parsing team metadata: {e}")
        
        name = team_meta.get("name", {})
        if isinstance(name, dict):
            name = name.get("full", "Unknown")
        logger.info(f"Retrieved metadata for team: {name}")
        return team_meta
    
    def get_team_roster(self, team_key: str, week: Optional[int] = None) -> dict:
        week_str = str(week) if week else "0"
        logger.info(f"Fetching roster for team: {team_key}, week: {week_str}")
        url = f"{self.base_url}/team/{team_key}/roster?format=json&week={week_str}"
        data = self._make_request("GET", url)
        
        parsed_players = []
        try:
            fc = data.get("fantasy_content", {})
            team_arr = fc.get("team", [])
            # Yahoo returns team as: [metadata_array, {roster: {...}}]
            if isinstance(team_arr, list) and len(team_arr) > 1:
                sub = team_arr[1]
                if isinstance(sub, dict):
                    # sub = {roster: {0: {players: {0: {player: [props, sub]}, ...}, count: N}}}
                    roster_container = sub.get("roster", {})
                    # Extract the first entry with "players" key
                    players_container = {}
                    if isinstance(roster_container, dict):
                        for k in sorted(roster_container.keys(), key=lambda x: (not str(x).isdigit(), int(x) if str(x).isdigit() else x)):
                            entry = roster_container[k]
                            if isinstance(entry, dict) and "players" in entry:
                                players_container = entry["players"]
                                break
                    
                    # Parse players from Yahoo's numeric-keyed container
                    if isinstance(players_container, dict):
                        for pk in sorted(players_container.keys(), key=lambda x: (not str(x).isdigit(), int(x) if str(x).isdigit() else x)):
                            player_entry = players_container[pk]
                            if not isinstance(player_entry, dict) or "player" not in player_entry:
                                continue
                            player_arr = player_entry["player"]
                            # player_arr is: [props_list_of_dicts, {sub_resources}]
                            if not isinstance(player_arr, list) or len(player_arr) < 1:
                                continue
                            props_list = player_arr[0]
                            player_data = {}
                            # props_list is a list of single-key dicts: [{name: {full: ...}}, {selected_position: ...}, ...]
                            if isinstance(props_list, list):
                                for prop in props_list:
                                    if isinstance(prop, dict):
                                        player_data.update(prop)
                            elif isinstance(props_list, dict):
                                player_data.update(props_list)
                            # Merge subresources (index 1) e.g. player_stats
                            if len(player_arr) > 1 and isinstance(player_arr[1], dict):
                                player_data.update(player_arr[1])
                            parsed_players.append(player_data)
            
            roster = {"players": parsed_players}
        except Exception as e:
            logger.warning(f"Error parsing roster: {e}")
            roster = {"players": parsed_players}
        
        logger.info(f"Retrieved roster with {len(parsed_players)} players")
        return roster

    def get_league_players(self, league_key: str, status: str = "FA", count: int = 200) -> list[dict]:
        """
        Get available/waiver players for a league.
        
        Status filters (Yahoo API valid values):
          'FA' - Free Agents (unowned players available to pick up)
          'W' - Waiver (players currently on waivers)
          'A' - All players
        
        Yahoo response format:
        fantasy_content.league = [
          {league_metadata},
          {players: {0: {player: [props, sub]}, count: N}}
        ]
        """
        logger.info(f"Fetching {status} players for league: {league_key}")
        url = f"{self.base_url}/league/{league_key}/players;status={status};count={count}?format=json"
        data = self._make_request("GET", url)
        
        parsed_players = []
        try:
            fc = data.get("fantasy_content", {})
            league_arr = fc.get("league", [])
            if not isinstance(league_arr, list) or len(league_arr) < 2:
                logger.warning(f"Unexpected league format for players: {type(league_arr)}")
                return []
            
            players_container = league_arr[1].get("players") if isinstance(league_arr[1], dict) else {}
            if not isinstance(players_container, dict):
                return []
            
            for pk in sorted(players_container.keys(), key=lambda x: (not str(x).isdigit(), int(x) if str(x).isdigit() else x)):
                player_entry = players_container[pk]
                if not isinstance(player_entry, dict) or "player" not in player_entry:
                    continue
                player_arr = player_entry["player"]
                if not isinstance(player_arr, list) or len(player_arr) < 1:
                    continue
                props_list = player_arr[0]
                player_data = {}
                if isinstance(props_list, list):
                    for prop in props_list:
                        if isinstance(prop, dict):
                            player_data.update(prop)
                elif isinstance(props_list, dict):
                    player_data.update(props_list)
                # Merge subresources
                if len(player_arr) > 1 and isinstance(player_arr[1], dict):
                    player_data.update(player_arr[1])
                parsed_players.append(player_data)
        except Exception as e:
            logger.warning(f"Error parsing league players: {e}")
        
        logger.info(f"Retrieved {len(parsed_players)} available players for league {league_key}")
        return parsed_players
    
    @staticmethod
    def _game_key_to_season(game_key: str) -> int:
        return 2026  # Default to current
    
    def sync_leagues(self) -> list[League]:
        logger.info("Syncing leagues from Yahoo")
        
        # Delete old leagues that may exist from previous incomplete syncs
        # Only keep leagues that exist in the current Yahoo season
        old_leagues = get_leagues_by_user(self.db, self.user.id)
        old_keys = {l.league_key for l in old_leagues}
        
        yahoo_leagues = self.get_leagues()
        new_keys = set()
        synced_leagues = []
        
        for league_data in yahoo_leagues:
            try:
                league_key = league_data.get("league_key") or league_data.get("key")
                if not league_key:
                    continue
                new_keys.add(league_key)
                name = league_data.get("name", "Unknown League")
                game_key = league_data.get("game_key") or league_key.split('.')[0]
                season = int(league_data.get("season", self._game_key_to_season(game_key)))
                num_teams = int(league_data.get("num_teams", 0))
                current_week = int(league_data.get("current_week", 0))
                
                league = upsert_league(
                    self.db,
                    user_id=self.user.id,
                    league_key=league_key,
                    name=name,
                    game_key=game_key,
                    season=season,
                    league_type=league_data.get("league_type", "full"),
                    num_teams=num_teams,
                    current_week=current_week,
                    start_week=int(league_data.get("start_week", 0)),
                    end_week=int(league_data.get("end_week", 0)),
                    league_data=league_data
                )
                synced_leagues.append(league)
            except Exception as e:
                logger.error(f"Error syncing league {league_data}: {e}")
        
        # Remove old leagues that are no longer in sync results
        stale_keys = old_keys - new_keys
        if stale_keys:
            logger.info(f"Removing {len(stale_keys)} stale leagues from database")
            for league in old_leagues:
                if league.league_key in stale_keys:
                    self.db.delete(league)
            self.db.commit()
        
        logger.info(f"Successfully synced {len(synced_leagues)} leagues")
        return synced_leagues
    
    def sync_team(self, team_key: str) -> Team:
        logger.info(f"Syncing team: {team_key}")
        team_data = self.get_team_metadata(team_key)
        name = team_data.get("name", "Unknown Team")
        league_key = team_key.rsplit('.t.', 1)[0]
        
        league = self.db.query(League).filter(League.league_key == league_key).first()
        if not league:
            game_key = team_key.split('.')[0]
            league = upsert_league(
                self.db,
                user_id=self.user.id,
                league_key=league_key,
                name=league_key,
                game_key=game_key,
                season=2026
            )
        
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
        logger.info(f"Syncing roster for team: {team.team_key}")
        roster_data = self.get_team_roster(team.team_key, week)
        if week is None:
            week = 0
        roster = save_roster(self.db, team.id, week, roster_data)
        return roster
    
    def sync_standings(self, league: League) -> Standings:
        logger.info(f"Syncing standings for league: {league.league_key}")
        standings_data = self.get_league_standings(league.league_key)
        standings = save_standings(self.db, league.id, standings_data)
        logger.info(f"Saved standings for {len(standings_data)} teams")
        return standings


def get_user_leagues(db: Session, user_id: int) -> list[dict]:
    leagues = get_leagues_by_user(db, user_id)
    return [league.to_dict() for league in leagues]


def get_user_teams(db: Session, user_id: int) -> list[dict]:
    teams = get_teams_by_user(db, user_id)
    return [team.to_dict() for team in teams]
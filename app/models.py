"""
Database models for Fantasy Baseball Assistant.
Stores OAuth tokens and Yahoo Fantasy data.
"""

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, JSON
from sqlalchemy.orm import Session

from app.db import Base

logger = logging.getLogger(__name__)


class User(Base):
    """User model storing Yahoo OAuth information."""
    
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    yahoo_guid = Column(String(100), unique=True, index=True, nullable=False)
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=False)
    token_expiry = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def is_token_expired(self) -> bool:
        """Check if the access token is expired."""
        return datetime.utcnow() >= self.token_expiry
    
    def to_dict(self) -> dict:
        """Convert to dictionary (excluding sensitive data)."""
        return {
            "id": self.id,
            "yahoo_guid": self.yahoo_guid,
            "token_expiry": self.token_expiry.isoformat() if self.token_expiry else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class League(Base):
    """Fantasy league model."""
    
    __tablename__ = "leagues"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    league_key = Column(String(100), unique=True, index=True, nullable=False)
    name = Column(String(255), nullable=False)
    game_key = Column(String(50), nullable=False)
    season = Column(Integer, nullable=False)
    league_type = Column(String(50), default="full")
    num_teams = Column(Integer, default=0)
    current_week = Column(Integer, default=0)
    start_week = Column(Integer, default=0)
    end_week = Column(Integer, default=0)
    league_data = Column(JSON, nullable=True)  # Full league metadata
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "league_key": self.league_key,
            "name": self.name,
            "game_key": self.game_key,
            "season": self.season,
            "num_teams": self.num_teams,
            "current_week": self.current_week,
        }


class Team(Base):
    """Fantasy team model."""
    
    __tablename__ = "teams"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    league_id = Column(Integer, nullable=False, index=True)
    team_key = Column(String(100), unique=True, index=True, nullable=False)
    name = Column(String(255), nullable=False)
    team_data = Column(JSON, nullable=True)  # Full team metadata
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "team_key": self.team_key,
            "name": self.name,
            "league_id": self.league_id,
        }


class Roster(Base):
    """Player roster snapshot."""
    
    __tablename__ = "rosters"
    
    id = Column(Integer, primary_key=True, index=True)
    team_id = Column(Integer, nullable=False, index=True)
    week = Column(Integer, nullable=False)
    roster_data = Column(JSON, nullable=True)  # Full roster metadata
    captured_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "team_id": self.team_id,
            "week": self.week,
            "captured_at": self.captured_at.isoformat() if self.captured_at else None,
        }


class Standings(Base):
    """League standings snapshot."""
    
    __tablename__ = "standings"
    
    id = Column(Integer, primary_key=True, index=True)
    league_id = Column(Integer, nullable=False, index=True)
    standings_data = Column(JSON, nullable=True)  # Full standings metadata
    captured_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "league_id": self.league_id,
            "captured_at": self.captured_at.isoformat() if self.captured_at else None,
        }


# CRUD operations for User
def get_user_by_guid(db: Session, yahoo_guid: str) -> Optional[User]:
    """Get a user by their Yahoo GUID."""
    return db.query(User).filter(User.yahoo_guid == yahoo_guid).first()


def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    """Get a user by their ID."""
    return db.query(User).filter(User.id == user_id).first()


def create_user(db: Session, yahoo_guid: str, access_token: str, refresh_token: str, 
                token_expiry: datetime) -> User:
    """Create a new user."""
    user = User(
        yahoo_guid=yahoo_guid,
        access_token=access_token,
        refresh_token=refresh_token,
        token_expiry=token_expiry
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info(f"Created new user with Yahoo GUID: {yahoo_guid}")
    return user


def update_user_token(db: Session, user: User, access_token: str, refresh_token: str,
                      token_expiry: datetime) -> User:
    """Update user's OAuth tokens."""
    user.access_token = access_token
    user.refresh_token = refresh_token
    user.token_expiry = token_expiry
    user.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(user)
    logger.info(f"Updated tokens for user: {user.yahoo_guid}")
    return user


# CRUD operations for League
def get_leagues_by_user(db: Session, user_id: int) -> list[League]:
    """Get all leagues for a user."""
    return db.query(League).filter(League.user_id == user_id).all()


def get_league_by_key(db: Session, league_key: str) -> Optional[League]:
    """Get a league by its key."""
    return db.query(League).filter(League.league_key == league_key).first()


def upsert_league(db: Session, user_id: int, league_key: str, name: str, 
                  game_key: str, season: int, **kwargs) -> League:
    """Insert or update a league."""
    league = get_league_by_key(db, league_key)
    if league:
        league.name = name
        league.game_key = game_key
        league.season = season
        for key, value in kwargs.items():
            if hasattr(league, key):
                setattr(league, key, value)
        league.updated_at = datetime.utcnow()
    else:
        league = League(
            user_id=user_id,
            league_key=league_key,
            name=name,
            game_key=game_key,
            season=season,
            **kwargs
        )
        db.add(league)
    db.commit()
    db.refresh(league)
    return league


# CRUD operations for Team
def get_teams_by_user(db: Session, user_id: int) -> list[Team]:
    """Get all teams for a user."""
    return db.query(Team).filter(Team.user_id == user_id).all()


def get_team_by_key(db: Session, team_key: str) -> Optional[Team]:
    """Get a team by its key."""
    return db.query(Team).filter(Team.team_key == team_key).first()


def upsert_team(db: Session, user_id: int, league_id: int, team_key: str,
                name: str, **kwargs) -> Team:
    """Insert or update a team."""
    team = get_team_by_key(db, team_key)
    if team:
        team.name = name
        team.league_id = league_id
        for key, value in kwargs.items():
            if hasattr(team, key):
                setattr(team, key, value)
        team.updated_at = datetime.utcnow()
    else:
        team = Team(
            user_id=user_id,
            league_id=league_id,
            team_key=team_key,
            name=name,
            **kwargs
        )
        db.add(team)
    db.commit()
    db.refresh(team)
    return team


# CRUD operations for Roster
def get_latest_roster(db: Session, team_id: int) -> Optional[Roster]:
    """Get the most recent roster snapshot for a team."""
    return db.query(Roster).filter(
        Roster.team_id == team_id
    ).order_by(Roster.captured_at.desc()).first()


def save_roster(db: Session, team_id: int, week: int, roster_data: dict) -> Roster:
    """Save a roster snapshot."""
    roster = Roster(
        team_id=team_id,
        week=week,
        roster_data=roster_data
    )
    db.add(roster)
    db.commit()
    db.refresh(roster)
    return roster


# CRUD operations for Standings
def get_latest_standings(db: Session, league_id: int) -> Optional[Standings]:
    """Get the most recent standings snapshot for a league."""
    return db.query(Standings).filter(
        Standings.league_id == league_id
    ).order_by(Standings.captured_at.desc()).first()


def save_standings(db: Session, league_id: int, standings_data: dict) -> Standings:
    """Save a standings snapshot."""
    standings = Standings(
        league_id=league_id,
        standings_data=standings_data
    )
    db.add(standings)
    db.commit()
    db.refresh(standings)
    return standings
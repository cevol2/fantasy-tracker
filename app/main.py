"""
Fantasy Baseball Assistant - Main Application
FastAPI backend with Yahoo OAuth integration
"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db, init_db, get_session_factory
from app.models import User, League, Team, get_user_by_id, get_user_by_guid, get_latest_roster, get_latest_standings
from app.yahoo_oauth import YahooOAuth, YahooOAuthError, get_yahoo_oauth
from app.yahoo_client import YahooFantasyClient, YahooAPIError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup and shutdown."""
    # Startup
    logger.info("Starting Fantasy Baseball Assistant")
    try:
        init_db()
        logger.info("Database initialized")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
    
    yield
    
    # Shutdown
    logger.info("Shutting down Fantasy Baseball Assistant")


# Create FastAPI app
app = FastAPI(
    title="Fantasy Baseball Assistant",
    description="Yahoo Fantasy Sports integration for fantasy baseball",
    version="1.0.0",
    lifespan=lifespan
)

# Mount static files
static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Add session middleware for OAuth state management
app.add_middleware(
    SessionMiddleware,
    secret_key=get_settings().SESSION_SECRET,
    max_age=86400 * 7  # 7 days
)


# Dependency to get current user
def get_current_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    """Get the current logged-in user from session."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return get_user_by_id(db, user_id)


# Routes
@app.get("/", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db)):
    """Home page - shows login or dashboard link."""
    user = get_current_user(request, db)
    
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Fantasy Baseball Assistant</title>
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                max-width: 800px;
                margin: 50px auto;
                padding: 20px;
                background: #1a1a2e;
                color: #eee;
            }
            .container {
                background: #16213e;
                padding: 30px;
                border-radius: 10px;
                text-align: center;
            }
            h1 { color: #e94560; }
            .btn {
                display: inline-block;
                padding: 15px 30px;
                margin: 10px;
                background: #e94560;
                color: white;
                text-decoration: none;
                border-radius: 5px;
                font-size: 18px;
            }
            .btn:hover { background: #c73e54; }
            .info { color: #888; margin-top: 20px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>⚾ Fantasy Baseball Assistant</h1>
            <p>Connect your Yahoo Fantasy Baseball account to get started.</p>
    """
    
    if user:
        html += f"""
            <p>Logged in as Yahoo user: <strong>{user.yahoo_guid[:12]}...</strong></p>
            <a href="/dashboard" class="btn">View Dashboard</a>
        """
    else:
        html += """
            <a href="/login" class="btn">🔗 Connect Yahoo</a>
        """
    
    html += """
            <p class="info">This app helps you manage your Yahoo Fantasy Baseball teams.</p>
        </div>
    </body>
    </html>
    """
    return html


@app.get("/login")
async def login(request: Request):
    """
    Initiate Yahoo OAuth flow.
    Redirects user to Yahoo authorization page.
    """
    logger.info("Login route accessed")
    
    try:
        oauth = get_yahoo_oauth()
        auth_url, state = oauth.get_authorization_url()
        
        # Store state in session for CSRF protection
        request.session["oauth_state"] = state
        
        logger.info(f"Redirecting to Yahoo auth: {state[:8]}...")
        return RedirectResponse(auth_url)
        
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        raise HTTPException(
            status_code=500,
            detail="OAuth configuration error. Please check your .env file."
        )
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to initiate login: {str(e)}"
        )


@app.get("/callback")
async def callback(request: Request, code: str = None, state: str = None, error: str = None, db: Session = Depends(get_db)):
    """
    OAuth callback route.
    Yahoo redirects back here after user authorizes the app.
    """
    logger.info("Callback route accessed")
    
    # Check for errors from Yahoo
    if error:
        logger.error(f"Yahoo OAuth error: {error}")
        raise HTTPException(
            status_code=400,
            detail=f"Yahoo authorization failed: {error}"
        )
    
    if not code:
        raise HTTPException(
            status_code=400,
            detail="Missing authorization code"
        )
    
    # Verify state for CSRF protection
    stored_state = request.session.get("oauth_state")
    if stored_state and state != stored_state:
        logger.warning("State mismatch - possible CSRF attack")
        raise HTTPException(
            status_code=400,
            detail="Invalid state parameter - possible CSRF attack"
        )
    
    try:
        oauth = get_yahoo_oauth()
        user = oauth.get_or_create_user(db, code)
        
        # Store user ID in session
        request.session["user_id"] = user.id
        request.session.pop("oauth_state", None)  # Clear state
        
        logger.info(f"OAuth flow complete for user: {user.yahoo_guid[:8]}...")
        
        return RedirectResponse("/dashboard")
        
    except YahooOAuthError as e:
        logger.error(f"OAuth error: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"OAuth failed: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Callback error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Callback processing failed: {str(e)}"
        )


@app.get("/logout")
async def logout(request: Request):
    """Log out the current user."""
    request.session.clear()
    return RedirectResponse("/")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    """
    Main dashboard showing connected status and Yahoo data.
    """
    user = get_current_user(request, db)
    
    if not user:
        return RedirectResponse("/login")
    
    # Get user's leagues
    leagues = db.query(League).filter(League.user_id == user.id).all()
    
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Dashboard - Fantasy Baseball Assistant</title>
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                max-width: 1200px;
                margin: 0 auto;
                padding: 20px;
                background: #1a1a2e;
                color: #eee;
            }
            .header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 20px 0;
                border-bottom: 1px solid #333;
            }
            h1 { color: #e94560; margin: 0; }
            .status {
                background: #16213e;
                padding: 20px;
                border-radius: 10px;
                margin: 20px 0;
            }
            .connected { color: #4ade80; }
            .leagues {
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
                gap: 20px;
                margin: 20px 0;
            }
            .league-card {
                background: #16213e;
                padding: 20px;
                border-radius: 10px;
                border: 1px solid #333;
            }
            .league-card h3 { color: #e94560; margin-top: 0; }
            .btn {
                display: inline-block;
                padding: 8px 16px;
                background: #e94560;
                color: white;
                text-decoration: none;
                border-radius: 5px;
                font-size: 14px;
                border: none;
                cursor: pointer;
            }
            .btn:hover { background: #c73e54; }
            .btn-secondary {
                background: #333;
                color: white;
                text-decoration: none;
                padding: 8px 16px;
                border-radius: 5px;
                font-size: 14px;
            }
            .info { color: #888; font-size: 14px; }
            table {
                width: 100%;
                border-collapse: collapse;
                margin-top: 10px;
            }
            th, td {
                padding: 10px;
                text-align: left;
                border-bottom: 1px solid #333;
            }
            th { color: #888; }
            .roster-table { margin-top: 20px; }
            .standings-table { margin-top: 20px; }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>⚾ Fantasy Baseball Dashboard</h1>
            <a href="/" class="btn-secondary">Home</a>
            <a href="/logout" class="btn-secondary">Logout</a>
        </div>
        
        <div class="status">
            <h2>Connection Status</h2>
            <p class="connected">✓ Connected to Yahoo</p>
            <p>Yahoo GUID: <code>""" + user.yahoo_guid + """</code></p>
            <p>Token expires: """ + user.token_expiry.strftime("%Y-%m-%d %H:%M:%S UTC") + """</p>
            <a href="/sync" class="btn">🔄 Sync from Yahoo</a>
        </div>
        
        <h2>Your Fantasy Leagues</h2>
    """
    
    if not leagues:
        html += """
        <p>No leagues found. <a href="/sync" class="btn">Sync from Yahoo</a></p>
        """
    else:
        html += '<div class="leagues">'
        for league in leagues:
            html += f"""
            <div class="league-card">
                <h3>{league.name}</h3>
                <p class="info">Key: {league.league_key}</p>
                <p>Teams: {league.num_teams} | Week: {league.current_week}</p>
                <button onclick="loadLeagueInfo('{league.league_key}')" class="btn">View Details</button>
                <a href="/sync/standings/{league.league_key}" class="btn">Standings</a>
            </div>
            """
        html += '</div>'
    
    html += """
        <div id="league-details" style="display:none;"></div>
        
        <script>
        function loadLeagueInfo(leagueKey) {
            fetch('/api/league/' + encodeURIComponent(leagueKey))
                .then(r => r.json())
                .then(data => {
                    const div = document.getElementById('league-details');
                    div.style.display = 'block';
                    div.innerHTML = '<h3>League Details: ' + data.name + '</h3><pre>' + JSON.stringify(data, null, 2) + '</pre>';
                });
        }
        </script>
    </body>
    </html>
    """
    return html


@app.get("/sync")
async def sync(request: Request, db: Session = Depends(get_db)):
    """
    Sync all Yahoo Fantasy data for the current user.
    """
    user = get_current_user(request, db)
    
    if not user:
        return RedirectResponse("/login")
    
    try:
        logger.info("Starting data sync")
        client = YahooFantasyClient(user, db)
        
        # Sync leagues
        leagues = client.sync_leagues()
        logger.info(f"Synced {len(leagues)} leagues")
        
        return {
            "status": "success",
            "message": f"Successfully synced {len(leagues)} leagues",
            "leagues_synced": len(leagues)
        }
        
    except YahooAPIError as e:
        logger.error(f"Sync failed: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Sync failed: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Unexpected sync error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Sync failed: {str(e)}"
        )


@app.get("/sync/roster/{team_key}", response_class=HTMLResponse)
async def sync_roster(team_key: str, request: Request, db: Session = Depends(get_db)):
    """
    Sync and display roster for a specific team.
    """
    user = get_current_user(request, db)
    
    if not user:
        return RedirectResponse("/login")
    
    try:
        client = YahooFantasyClient(user, db)
        
        # Get or sync team
        team = db.query(Team).filter(Team.team_key == team_key).first()
        if not team:
            team = client.sync_team(team_key)
        
        # Sync roster
        roster = client.sync_roster(team)
        
        # Format response
        players = roster.roster_data.get("players", {}).get("player", [])
        if not isinstance(players, list):
            players = [players] if players else []
        
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Roster - """ + team.name + """</title>
            <style>
                body {
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    max-width: 1200px;
                    margin: 0 auto;
                    padding: 20px;
                    background: #1a1a2e;
                    color: #eee;
                }
                table { width: 100%; border-collapse: collapse; }
                th, td { padding: 12px; text-align: left; border-bottom: 1px solid #333; }
                th { color: #888; background: #16213e; }
                .btn {
                    display: inline-block;
                    padding: 8px 16px;
                    background: #e94560;
                    color: white;
                    text-decoration: none;
                    border-radius: 5px;
                    margin: 10px 0;
                }
            </style>
        </head>
        <body>
            <a href="/dashboard" class="btn">← Back to Dashboard</a>
            <h1>Roster: """ + team.name + """</h1>
            <p>Week: """ + str(roster.week) + """ | Captured: """ + roster.captured_at.strftime("%Y-%m-%d %H:%M") + """</p>
            
            <table>
                <tr>
                    <th>Player</th>
                    <th>Position</th>
                    <th>Status</th>
                    <th>Eligible Positions</th>
                </tr>
        """
        
        for player in players:
            name = player.get("name", {}).get("full", "Unknown")
            pos = player.get("selected_position", {}).get("position", "N/A")
            status = player.get("status", "-")
            eligible = player.get("eligible_positions", {}).get("position", "-")
            
            html += f"""
                <tr>
                    <td>{name}</td>
                    <td>{pos}</td>
                    <td>{status}</td>
                    <td>{eligible if isinstance(eligible, str) else ', '.join(eligible) if isinstance(eligible, list) else '-'}</td>
                </tr>
            """
        
        html += """
            </table>
        </body>
        </html>
        """
        return html
        
    except Exception as e:
        logger.error(f"Roster sync error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sync/standings/{league_key}", response_class=HTMLResponse)
async def sync_standings(league_key: str, request: Request, db: Session = Depends(get_db)):
    """
    Sync and display standings for a specific league.
    """
    user = get_current_user(request, db)
    
    if not user:
        return RedirectResponse("/login")
    
    try:
        client = YahooFantasyClient(user, db)
        
        # Get or sync league
        league = db.query(League).filter(League.league_key == league_key).first()
        if not league:
            client.sync_leagues()
            league = db.query(League).filter(League.league_key == league_key).first()
        
        if not league:
            raise HTTPException(status_code=404, detail="League not found")
        
        # Sync standings
        standings = client.sync_standings(league)
        
        # Format response
        teams = standings.standings_data if isinstance(standings.standings_data, list) else [standings.standings_data]
        
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Standings - """ + league.name + """</title>
            <style>
                body {
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    max-width: 1200px;
                    margin: 0 auto;
                    padding: 20px;
                    background: #1a1a2e;
                    color: #eee;
                }
                table { width: 100%; border-collapse: collapse; }
                th, td { padding: 12px; text-align: left; border-bottom: 1px solid #333; }
                th { color: #888; background: #16213e; }
                .btn {
                    display: inline-block;
                    padding: 8px 16px;
                    background: #e94560;
                    color: white;
                    text-decoration: none;
                    border-radius: 5px;
                    margin: 10px 0;
                }
            </style>
        </head>
        <body>
            <a href="/dashboard" class="btn">← Back to Dashboard</a>
            <h1>Standings: """ + league.name + """</h1>
            <p>Captured: """ + standings.captured_at.strftime("%Y-%m-%d %H:%M") + """</p>
            
            <table>
                <tr>
                    <th>Rank</th>
                    <th>Team</th>
                    <th>Wins</th>
                    <th>Losses</th>
                    <th>Win %</th>
                    <th>Games Back</th>
                </tr>
        """
        
        rank = 1
        for team_data in teams:
            if not team_data:
                continue
            name = team_data.get("name", {}).get("full", "Unknown") if isinstance(team_data.get("name"), dict) else team_data.get("name", "Unknown")
            stats = team_data.get("team_stats", {}).get("stats", {}).get("stat", [])
            
            wins = 0
            losses = 0
            win_pct = 0
            games_back = "-"
            
            for stat in stats if isinstance(stats, list) else []:
                stat_id = stat.get("stat_id") or stat.get("@id")
                value = stat.get("value", "0")
                if str(stat_id) == "0":  # Wins
                    wins = int(value)
                elif str(stat_id) == "1":  # Losses
                    losses = int(value)
                elif str(stat_id) == "2":  # Win %
                    win_pct = float(value) if value else 0
            
            html += f"""
                <tr>
                    <td>{rank}</td>
                    <td>{name}</td>
                    <td>{wins}</td>
                    <td>{losses}</td>
                    <td>{win_pct:.3f}</td>
                    <td>{games_back}</td>
                </tr>
            """
            rank += 1
        
        html += """
            </table>
        </body>
        </html>
        """
        return html
        
    except Exception as e:
        logger.error(f"Standings sync error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# API routes for AJAX calls
@app.get("/api/user")
async def api_user(request: Request, db: Session = Depends(get_db)):
    """Get current user info."""
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    return user.to_dict()


@app.get("/api/leagues")
async def api_leagues(request: Request, db: Session = Depends(get_db)):
    """Get user's leagues."""
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    
    leagues = db.query(League).filter(League.user_id == user.id).all()
    return [league.to_dict() for league in leagues]


@app.get("/api/league/{league_key}")
async def api_league(league_key: str, request: Request, db: Session = Depends(get_db)):
    """Get league details."""
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    
    league = db.query(League).filter(League.league_key == league_key).first()
    if not league:
        raise HTTPException(status_code=404, detail="League not found")
    
    return league.to_dict()


@app.get("/api/teams")
async def api_teams(request: Request, db: Session = Depends(get_db)):
    """Get user's teams."""
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    
    teams = db.query(Team).filter(Team.user_id == user.id).all()
    return [team.to_dict() for team in teams]


if __name__ == "__main__":
    import uvicorn
    settings = get_settings()

    if settings.YAHOO_REDIRECT_URI.startswith("https://") and not (
        settings.SSL_CERTFILE and settings.SSL_KEYFILE
    ):
        logger.warning(
            "YAHOO_REDIRECT_URI is https but SSL_CERTFILE/SSL_KEYFILE are not configured. "
            "This may cause OAuth callback failures for local HTTPS redirects."
        )

    uvicorn_kwargs = {
        "host": settings.HOST,
        "port": settings.PORT,
    }

    if settings.SSL_CERTFILE and settings.SSL_KEYFILE:
        uvicorn_kwargs["ssl_certfile"] = settings.SSL_CERTFILE
        uvicorn_kwargs["ssl_keyfile"] = settings.SSL_KEYFILE
        logger.info(
            "Starting Fantasy Baseball Assistant with HTTPS on %s:%s",
            settings.HOST,
            settings.PORT,
        )
    else:
        logger.info(
            "Starting Fantasy Baseball Assistant without HTTPS on %s:%s",
            settings.HOST,
            settings.PORT,
        )

    uvicorn.run(app, **uvicorn_kwargs)
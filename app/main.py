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
        <a href="/standings" class="btn">📊 View All Standings</a>
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
    Redirects back to dashboard after sync.
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
        
        return RedirectResponse("/dashboard")
        
    except YahooAPIError as e:
        logger.error(f"Sync failed: {e}")
        request.session["sync_error"] = str(e)
        return RedirectResponse("/dashboard")
    except Exception as e:
        logger.error(f"Unexpected sync error: {e}")
        request.session["sync_error"] = str(e)
        return RedirectResponse("/dashboard")


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


@app.get("/standings", response_class=HTMLResponse)
async def view_all_standings(request: Request, db: Session = Depends(get_db)):
    """
    Show standings for all leagues.
    """
    user = get_current_user(request, db)
    
    if not user:
        return RedirectResponse("/login")
    
    # Get all leagues for this user
    leagues = db.query(League).filter(League.user_id == user.id).all()
    
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>All Standings - Fantasy Baseball Assistant</title>
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                max-width: 1200px;
                margin: 0 auto;
                padding: 20px;
                background: #1a1a2e;
                color: #eee;
            }
            .league-standings {
                background: #16213e;
                padding: 20px;
                border-radius: 10px;
                margin: 20px 0;
                border: 1px solid #333;
            }
            h1 { color: #e94560; }
            h2 { color: #e94560; border-bottom: 1px solid #333; padding-bottom: 10px; }
            table { width: 100%; border-collapse: collapse; margin-top: 10px; }
            th, td { padding: 12px; text-align: left; border-bottom: 1px solid #333; }
            th { color: #888; background: #0f3460; }
            tr:nth-child(even) { background: #0f3460; }
            .btn {
                display: inline-block;
                padding: 8px 16px;
                background: #e94560;
                color: white;
                text-decoration: none;
                border-radius: 5px;
                margin: 10px 0;
            }
            .btn-secondary {
                background: #333;
                color: white;
                text-decoration: none;
                padding: 8px 16px;
                border-radius: 5px;
            }
            .header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 20px 0;
                border-bottom: 1px solid #333;
            }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>🏆 All League Standings</h1>
            <div>
                <a href="/dashboard" class="btn-secondary">Dashboard</a>
                <a href="/sync" class="btn">🔄 Sync Standings</a>
            </div>
        </div>
    """
    
    for league in leagues:
        html += f"""
        <div class="league-standings">
            <h2>{league.name}</h2>
            <p>Current Week: {league.current_week} | Teams: {league.num_teams}</p>
            <a href="/sync/standings/{league.league_key}" class="btn">Refresh Standings</a>
            <table>
                <tr>
                    <th>Rank</th>
                    <th>Team</th>
                    <th>Total</th>
                    <th></th>
                </tr>
        """
        
        # Get cached standings from DB
        standings_record = get_latest_standings(db, league.id)
        if standings_record and isinstance(standings_record.standings_data, list):
            teams = standings_record.standings_data
            
            # Check if this is a category league (team_points.total is always "0")
            is_category_league = False
            for t in teams:
                tp = t.get("team_points", {})
                total = tp.get("total") if isinstance(tp, dict) else None
                if total and float(total) > 0:
                    is_category_league = False
                    break
                elif total and float(total) == 0 and t.get("league_scoring_type") == "head":
                    is_category_league = True
            
            # Stat name mapping for category leagues
            cat_names = {
                "7": "R", "12": "HR", "13": "RBI", "16": "SB",
                "4": "AVG", "5": "OBP", "3": "BA", "55": "OPS",
                "50": "IP", "26": "ERA", "27": "WHIP",
                "57": "K", "83": "SV", "89": "HLD",
                "28": "W", "32": "QS", "22": "K"
            }
            
            if is_category_league:
                # Collect all stat categories present
                stat_ids = []
                for t in teams:
                    ts = t.get("team_stats", {})
                    stats_arr = ts.get("stats", []) if isinstance(ts, dict) else []
                    for s_entry in stats_arr:
                        if isinstance(s_entry, dict):
                            sid = s_entry.get("stat", {}).get("stat_id")
                            if sid and sid not in stat_ids:
                                stat_ids.append(sid)
                
                # Add extra stat columns
                html += '<tr><th>Rk</th><th>Team</th>'
                for sid in stat_ids:
                    name = cat_names.get(sid, sid)
                    html += f'<th>{name}</th>'
                html += '</tr>'
                
                for team_data in teams:
                    name = team_data.get("name", "Unknown")
                    ts = team_data.get("team_stats", {})
                    stats_arr = ts.get("stats", []) if isinstance(ts, dict) else []
                    stats_map = {}
                    for s_entry in stats_arr:
                        if isinstance(s_entry, dict):
                            s = s_entry.get("stat", {})
                            sid = s.get("stat_id")
                            val = s.get("value", "")
                            if sid:
                                stats_map[sid] = val if val else "-"
                    
                    html += '<tr>'
                    html += f'<td>{teams.index(team_data) + 1}</td>'
                    html += f'<td>{name}</td>'
                    for sid in stat_ids:
                        html += f'<td>{stats_map.get(sid, "-")}</td>'
                    html += '</tr>'
            else:
                # Points league - sort by total points desc
                def _sort_key(t):
                    tp = t.get("team_points", {})
                    total = tp.get("total") if isinstance(tp, dict) else None
                    return -float(total) if total else 0
                
                sorted_teams = sorted(teams, key=_sort_key)
                
                rank = 1
                for team_data in sorted_teams:
                    name = team_data.get("name", "Unknown")
                    team_points = team_data.get("team_points", {})
                    total = float(team_points.get("total", 0)) if isinstance(team_points, dict) else 0
                    
                    html += f"""
                        <tr>
                            <td>{rank}</td>
                            <td>{name}</td>
                            <td><strong>{total:.1f}</strong></td>
                            <td>pts</td>
                        </tr>
                    """
                    rank += 1
        else:
            html += '<tr><td colspan="4">No standings data available. <a href="/sync/standings/{league.league_key}">Sync now</a></td></tr>'
        
        html += "</table></div>"
    
    html += """
    </body>
    </html>
    """
    return html


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
        
        # Detect league type
        is_category_league = False
        for t in teams:
            tp = t.get("team_points", {})
            total = tp.get("total") if isinstance(tp, dict) else None
            if total and float(total) == 0 and t.get("league_scoring_type") == "head":
                is_category_league = True
        
        # Stat name mapping for category leagues
        cat_names = {
            "7": "R", "12": "HR", "13": "RBI", "16": "SB",
            "4": "AVG", "5": "OBP", "3": "BA", "55": "OPS",
            "50": "IP", "26": "ERA", "27": "WHIP",
            "57": "K", "83": "SV", "89": "HLD",
            "28": "W", "32": "QS", "22": "K"
        }
        
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Standings - """ + league.name + """</title>
            <style>
                body {
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    max-width: 1400px;
                    margin: 0 auto;
                    padding: 20px;
                    background: #1a1a2e;
                    color: #eee;
                }
                table { width: 100%; border-collapse: collapse; font-size: 14px; }
                th, td { padding: 10px 8px; text-align: left; border-bottom: 1px solid #333; white-space: nowrap; }
                th { color: #888; background: #16213e; position: sticky; top: 0; }
                tr:nth-child(even) { background: rgba(15, 52, 96, 0.3); }
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
            <a href="/standings" class="btn">← All Standings</a>
            <h1>Standings: """ + league.name + """</h1>
            <p>Captured: """ + standings.captured_at.strftime("%Y-%m-%d %H:%M") + """ | Type: """ + ("Category" if is_category_league else "Points") + """ League</p>
        """
        
        if is_category_league:
            # Collect all stat categories present
            stat_ids = []
            for t in teams:
                ts = t.get("team_stats", {})
                stats_arr = ts.get("stats", []) if isinstance(ts, dict) else []
                for s_entry in stats_arr:
                    if isinstance(s_entry, dict):
                        sid = s_entry.get("stat", {}).get("stat_id")
                        if sid and sid not in stat_ids:
                            stat_ids.append(sid)
            
            html += """<div style="overflow-x: auto;"><table><tr><th>Rk</th><th>Team</th>"""
            for sid in stat_ids:
                name = cat_names.get(sid, sid)
                html += f'<th>{name}</th>'
            html += '</tr>'
            
            for i, team_data in enumerate(teams):
                name = team_data.get("name", "Unknown")
                if isinstance(name, dict):
                    name = name.get("full", "Unknown")
                ts = team_data.get("team_stats", {})
                stats_arr = ts.get("stats", []) if isinstance(ts, dict) else []
                stats_map = {}
                for s_entry in stats_arr:
                    if isinstance(s_entry, dict):
                        s = s_entry.get("stat", {})
                        sid = s.get("stat_id")
                        val = s.get("value", "")
                        if sid:
                            stats_map[sid] = val if val else "-"
                
                html += '<tr>'
                html += f'<td>{i + 1}</td>'
                html += f'<td><strong>{name}</strong></td>'
                for sid in stat_ids:
                    html += f'<td>{stats_map.get(sid, "-")}</td>'
                html += '</tr>'
            
            html += '</table></div>'
        else:
            # Points league table
            html += """
            <table>
                <tr>
                    <th>Rank</th>
                    <th>Team</th>
                    <th>Total</th>
                    <th></th>
                </tr>
            """
            
            # Sort by total points desc
            def _sort_key(t):
                tp = t.get("team_points", {})
                total = tp.get("total") if isinstance(tp, dict) else None
                return -float(total) if total else 0
            
            sorted_teams = sorted(teams, key=_sort_key)
            
            rank = 1
            for team_data in sorted_teams:
                name = team_data.get("name", "Unknown")
                if isinstance(name, dict):
                    name = name.get("full", "Unknown")
                team_points = team_data.get("team_points", {})
                total = float(team_points.get("total", 0)) if isinstance(team_points, dict) else 0
                
                html += f"""
                    <tr>
                        <td>{rank}</td>
                        <td><strong>{name}</strong></td>
                        <td>{total:.1f}</td>
                        <td>pts</td>
                    </tr>
                """
                rank += 1
            
            html += "</table>"
        
        html += """
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
    uvicorn.run(app, host="0.0.0.0", port=8000)
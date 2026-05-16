# Fantasy Baseball Assistant

A FastAPI-based web application that integrates with Yahoo Fantasy Sports API to help you manage your fantasy baseball teams.

## Features

- **Yahoo OAuth Authentication** - Secure login with your Yahoo account
- **League Management** - View and sync your fantasy baseball leagues
- **Team Roster** - View your team rosters with player details
- **League Standings** - Track team rankings in your leagues
- **Automatic Token Refresh** - Tokens are automatically refreshed when expired

## Prerequisites

- Python 3.9+
- A Yahoo Developer account
- pip (Python package manager)

## Setup Instructions

### Step 1: Create a Yahoo Developer App

1. Go to [Yahoo Developer Network](https://developer.yahoo.com/apps/)
2. Click **"Create an App"**
3. Fill in the application details:
   - **Application Name**: Fantasy Baseball Assistant (or any name you prefer)
   - **Application Type**: Web Application
   - **Description**: A fantasy baseball assistant app

4. **Important: Configure the Redirect URI**
   
   In the "API Permissions" section or during app creation, you need to add:
   
   ```
   http://localhost:8000/callback
   ```
   
   This is the exact URL where Yahoo will redirect after authentication.

5. **Select Permissions**
   
   Enable the following permissions:
   - **Fantasy Sports** (read/write access)
   
6. **Note your credentials**
   
   After creating the app, copy:
   - **Client ID** (shown as "Consumer Key")
   - **Client Secret** (shown as "Consumer Secret")

### Step 2: Configure Environment Variables

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```

2. Edit the `.env` file and add your Yahoo credentials:
   ```env
   YAHOO_CLIENT_ID=your_client_id_here
   YAHOO_CLIENT_SECRET=your_client_secret_here
   YAHOO_REDIRECT_URI=https://localhost:8000/callback
   SSL_CERTFILE=./cert.pem
   SSL_KEYFILE=./key.pem
   ```

   If you are testing locally, the repository includes `cert.pem` and `key.pem` for HTTPS on `localhost:8000`.

### Step 3: Install Dependencies

```bash
# Create a virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Step 4: Run the Application

```bash
# Start the development server
python -m app.main
```

Or using uvicorn directly:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Step 5: Access the Application

Open your browser and navigate to:
```
http://localhost:8000
```

Click **"Connect Yahoo"** to initiate the OAuth login flow.

## Project Structure

```
.
├── app/
│   ├── __init__.py          # Package initialization
│   ├── config.py            # Environment configuration
│   ├── db.py                # Database setup and sessions
│   ├── main.py              # FastAPI application and routes
│   ├── models.py            # SQLAlchemy models and CRUD
│   ├── yahoo_client.py      # Yahoo Fantasy API client
│   └── yahoo_oauth.py       # Yahoo OAuth handler
├── static/                  # Static files (CSS, JS)
├── data/                    # SQLite database storage
├── .env                     # Environment variables (local only)
├── .env.example            # Example environment file
├── requirements.txt        # Python dependencies
└── README.md               # This file
```

## API Routes

| Route | Method | Description |
|-------|--------|-------------|
| `/` | GET | Home page |
| `/login` | GET | Initiate Yahoo OAuth |
| `/callback` | GET | OAuth callback handler |
| `/logout` | GET | Log out current user |
| `/dashboard` | GET | Main dashboard with leagues |
| `/sync` | GET | Sync all data from Yahoo |
| `/sync/roster/{team_key}` | GET | View team roster |
| `/sync/standings/{league_key}` | GET | View league standings |
| `/api/user` | GET | Get current user info |
| `/api/leagues` | GET | Get user's leagues |
| `/api/teams` | GET | Get user's teams |

## Yahoo API Endpoints

The app uses the following Yahoo Fantasy Sports API endpoints:

- `GET /ws/v1/user` - Get user info (GUID)
- `GET /ws/v1/user?game_keys=mlb&use_login=1` - Get MLB games
- `GET /ws/v1/game/{game_key}/league/{league_key}` - League metadata
- `GET /ws/v1/game/{game_key}/league/{league_key}/standings` - League standings
- `GET /ws/v1/game/{game_key}/team/{team_key}` - Team metadata
- `GET /ws/v1/game/{game_key}/team/{team_key}/roster` - Team roster

## Troubleshooting

### "OAuth configuration error"

**Cause**: The `.env` file is missing or has incorrect values.

**Solution**:
1. Verify your `.env` file exists and has correct values
2. Ensure `YAHOO_CLIENT_ID` and `YAHOO_CLIENT_SECRET` are set
3. Make sure there are no trailing spaces or quotes around values

### "Token exchange failed"

**Cause**: The redirect URI doesn't match the one registered in Yahoo Developer Console.

**Solution**:
1. Go to your Yahoo Developer App settings
2. Verify the redirect URI is exactly: `http://localhost:8000/callback`
3. Update the URI if necessary and save

### "Failed to get Yahoo GUID"

**Cause**: Authentication issue or expired tokens.

**Solution**:
1. Try logging out and logging back in
2. Clear your browser's session storage
3. Check if your Yahoo account has Fantasy Sports access

### "API request failed"

**Cause**: Network issues or Yahoo API rate limiting.

**Solution**:
1. Wait a few minutes and try again
2. Check your internet connection
3. Visit [Yahoo Fantasy Sports API Status](https://developer.yahoo.com/docs/status) to check for outages

### "Permission denied"

**Cause**: The app doesn't have the required permissions.

**Solution**:
1. Go to your Yahoo Developer App
2. Navigate to "Permissions" or "API Permissions"
3. Ensure "Fantasy Sports" is enabled with appropriate access

### Database Errors

**Cause**: Database file locked or corrupted.

**Solution**:
1. Stop the application
2. Delete the `data/fantasy_assistant.db` file
3. Restart the application (database will be recreated)

## Development

### Running Tests

```bash
# Install test dependencies
pip install pytest pytest-asyncio httpx

# Run tests
pytest
```

### Database Management

The SQLite database is stored at `data/fantasy_assistant.db`. To reset:

```bash
rm data/fantasy_assistant.db
python -c "from app.db import init_db; init_db()"
```

## Security Notes

- **Never commit `.env`** to version control
- Tokens are stored in the database, not in cookies
- Access tokens have a limited lifetime and are automatically refreshed
- Refresh tokens persist until explicitly revoked

## License

MIT License - See LICENSE file for details.

## Support

For issues with the Yahoo Developer API, visit [Yahoo Developer Support](https://developer.yahoo.com/apps/myapps/support).

For issues with this application, please open an issue on the project repository.
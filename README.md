# Jotto Duel

Jotto Duel is a dependency-free multiplayer Jotto web app built with Python, SQLite, and plain browser JavaScript.

## What changed

- Accounts: players can sign up, log in, and resume their rooms later
- Matchmaking: public quick-match support plus private friend rooms
- Persistence: rooms, guesses, rounds, and win history are stored in SQLite
- Better game rules: larger built-in dictionary, duplicate-guess blocking, alternating round starters, multi-round rooms
- Deployment: Dockerfile included and the app respects `PORT`

## Run locally

```bash
python3 app.py
```

Open `http://localhost:8000`.

The SQLite database lives at `state/jotto.db` by default. You can override it with:

```bash
JOTTO_DB_PATH=/some/path/jotto.db python3 app.py
```

## Optional SMS turn alerts

If you want players to get a text when it becomes their turn, set these environment variables:

```bash
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_VERIFY_SERVICE_SID=...
TWILIO_FROM_NUMBER=+15551234567
PUBLIC_BASE_URL=https://your-app-url.example.com
```

Then in the lobby, a player can:

1. enter a phone number in E.164 format
2. save it
3. request a verification code
4. verify the code
5. opt in to turn alerts

The app sends texts only to verified, opted-in numbers.

## Deploy it

### Option 1: Any host that runs Python directly

Use this start command:

```bash
python3 app.py
```

Set `PORT` if your host provides one. The app automatically listens on that port.

### Option 2: Docker

```bash
docker build -t jotto-duel .
docker run -p 8000:8000 jotto-duel
```

If you want persistent data in Docker, mount a volume for `/app/state`, or set `JOTTO_DB_PATH` to a mounted location.

### Option 3: Render

This repo now includes [render.yaml](./render.yaml), which configures:

- Docker-based deploys
- A persistent disk mounted at `/var/data`
- `JOTTO_DB_PATH=/var/data/jotto.db`

Typical flow:

1. Push this repo to GitHub.
2. In Render, create a new Blueprint from the repo.
3. Review the generated `jotto-duel` web service.
4. Deploy.

The dictionary still comes from bundled app data and system dictionaries, so only the SQLite file needs persistent storage.

## Product flow

1. Create an account or log in.
2. Create a private room and send the invite link to a friend, or click the public matchmaking button.
3. Both players lock in secret 5-letter isograms.
4. Take turns guessing. The score is the count of shared letters with the opponent’s secret.
5. Exact matches win the round.
6. Start a new round in the same room whenever you want.

## Notes

- This app uses polling instead of websockets to stay dependency-free.
- Room and account data persist between restarts as long as the SQLite file persists.
- The app prefers system dictionaries in `/usr/share/dict` when available, and falls back to `data/words.txt`.

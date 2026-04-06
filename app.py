import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse


BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
BUNDLED_DATA_DIR = BASE_DIR / "data"
STATE_DIR = Path(os.environ.get("JOTTO_STATE_DIR", BASE_DIR / "state"))
STATE_DIR.mkdir(exist_ok=True)

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8000"))
DB_PATH = Path(os.environ.get("JOTTO_DB_PATH", STATE_DIR / "jotto.db"))
ROOM_CODE_LENGTH = 6
PLAYER_LIMIT = 2
SESSION_COOKIE = "jotto_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,20}$")


def load_words() -> list[str]:
    candidate_paths = [
        Path("/usr/share/dict/words"),
        Path("/usr/share/dict/web2"),
        Path("/usr/share/dict/web2a"),
        BUNDLED_DATA_DIR / "words.txt",
    ]
    words = set()
    for words_path in candidate_paths:
        if not words_path.exists():
            continue
        for raw_line in words_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            raw_word = raw_line.strip()
            if raw_word != raw_word.lower():
                continue
            word = raw_word.lower()
            if len(word) == 5 and word.isalpha() and len(set(word)) == 5:
                words.add(word)
    return sorted(words)


WORD_LIST = load_words()
WORD_SET = set(WORD_LIST)


def now_ts() -> float:
    return time.time()


def normalize_word(word: str) -> str:
    return str(word).strip().lower()


def is_valid_word(word: str) -> bool:
    word = normalize_word(word)
    return len(word) == 5 and word.isalpha() and len(set(word)) == 5 and word in WORD_SET


def common_letter_score(guess: str, secret: str) -> int:
    return len(set(guess) & set(secret))


def generate_code(existing_codes: set[str]) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(ROOM_CODE_LENGTH))
        if code not in existing_codes:
            return code


def clean_name(name_value) -> str:
    if not isinstance(name_value, str):
        return ""
    return name_value.strip()[:20]


def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
    return digest.hex(), salt


def parse_json_body(handler: BaseHTTPRequestHandler) -> Optional[dict]:
    content_length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(content_length) if content_length > 0 else b"{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


class GameStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    password_salt TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS rooms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_code TEXT NOT NULL UNIQUE,
                    visibility TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_by_user_id INTEGER NOT NULL REFERENCES users(id),
                    round_number INTEGER NOT NULL DEFAULT 1,
                    current_turn_user_id INTEGER REFERENCES users(id),
                    winner_user_id INTEGER REFERENCES users(id),
                    winning_word TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS room_players (
                    room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    seat_order INTEGER NOT NULL,
                    secret_word TEXT,
                    joined_at REAL NOT NULL,
                    PRIMARY KEY (room_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS guesses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
                    round_number INTEGER NOT NULL,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    guess_word TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS round_results (
                    room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
                    round_number INTEGER NOT NULL,
                    winner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    winning_word TEXT NOT NULL,
                    losing_word TEXT,
                    finished_at REAL NOT NULL,
                    PRIMARY KEY (room_id, round_number)
                );
                """
            )
            round_result_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(round_results)").fetchall()
            }
            if "losing_word" not in round_result_columns:
                conn.execute("ALTER TABLE round_results ADD COLUMN losing_word TEXT")

    def create_user(self, username: str, password: str) -> tuple[Optional[dict], str]:
        username = username.strip()
        if not USERNAME_RE.match(username):
            return None, "Username must be 3-20 characters using letters, numbers, or underscores."
        if len(password) < 6:
            return None, "Password must be at least 6 characters."

        password_hash, salt = hash_password(password)
        with self.lock, self._connect() as conn:
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO users (username, password_hash, password_salt, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (username, password_hash, salt, now_ts()),
                )
            except sqlite3.IntegrityError:
                return None, "That username is already taken."

            user_id = cursor.lastrowid
            return self._session_for_user(conn, user_id), ""

    def authenticate_user(self, username: str, password: str) -> tuple[Optional[dict], str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
                (username.strip(),),
            ).fetchone()
            if not row:
                return None, "No account matched that username."

            expected_hash, _salt = hash_password(password, row["password_salt"])
            if expected_hash != row["password_hash"]:
                return None, "Password was incorrect."

            return self._session_for_user(conn, row["id"]), ""

    def _session_for_user(self, conn: sqlite3.Connection, user_id: int) -> dict:
        token = secrets.token_urlsafe(24)
        conn.execute("INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)", (token, user_id, now_ts()))
        user = conn.execute("SELECT id, username, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
        return {
            "session_token": token,
            "user": {
                "id": user["id"],
                "username": user["username"],
                "created_at": user["created_at"],
            },
        }

    def delete_session(self, token: str):
        if not token:
            return
        with self.lock, self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))

    def get_user_by_session(self, token: str) -> Optional[dict]:
        if not token:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT users.id, users.username, users.created_at
                FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.token = ?
                """,
                (token,),
            ).fetchone()
            return dict(row) if row else None

    def create_room(self, user_id: int, visibility: str) -> str:
        with self.lock, self._connect() as conn:
            room_code = generate_code({row["room_code"] for row in conn.execute("SELECT room_code FROM rooms")})
            timestamp = now_ts()
            cursor = conn.execute(
                """
                INSERT INTO rooms (
                    room_code, visibility, status, created_by_user_id, round_number,
                    current_turn_user_id, winner_user_id, winning_word, created_at, updated_at
                )
                VALUES (?, ?, 'waiting', ?, 1, NULL, NULL, NULL, ?, ?)
                """,
                (room_code, visibility, user_id, timestamp, timestamp),
            )
            room_id = cursor.lastrowid
            conn.execute(
                """
                INSERT INTO room_players (room_id, user_id, seat_order, secret_word, joined_at)
                VALUES (?, ?, 1, NULL, ?)
                """,
                (room_id, user_id, timestamp),
            )
            return room_code

    def join_room(self, user_id: int, room_code: str) -> tuple[Optional[str], str]:
        room_code = room_code.strip().upper()
        with self.lock, self._connect() as conn:
            room = conn.execute("SELECT * FROM rooms WHERE room_code = ?", (room_code,)).fetchone()
            if not room:
                return None, "Room not found."

            existing = conn.execute(
                "SELECT 1 FROM room_players WHERE room_id = ? AND user_id = ?",
                (room["id"], user_id),
            ).fetchone()
            if existing:
                return room_code, ""

            player_count = conn.execute(
                "SELECT COUNT(*) AS count FROM room_players WHERE room_id = ?",
                (room["id"],),
            ).fetchone()["count"]
            if player_count >= PLAYER_LIMIT:
                return None, "That room already has two players."

            conn.execute(
                """
                INSERT INTO room_players (room_id, user_id, seat_order, secret_word, joined_at)
                VALUES (?, ?, ?, NULL, ?)
                """,
                (room["id"], user_id, player_count + 1, now_ts()),
            )
            conn.execute(
                "UPDATE rooms SET status = 'setup', updated_at = ? WHERE id = ?",
                (now_ts(), room["id"]),
            )
            return room_code, ""

    def find_or_create_match(self, user_id: int) -> tuple[str, str]:
        with self.lock, self._connect() as conn:
            existing = conn.execute(
                """
                SELECT rooms.room_code
                FROM rooms
                JOIN room_players ON room_players.room_id = rooms.id
                WHERE room_players.user_id = ?
                  AND rooms.visibility = 'public'
                  AND rooms.status IN ('waiting', 'setup', 'playing')
                ORDER BY rooms.updated_at DESC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            if existing:
                return existing["room_code"], ""

            room = conn.execute(
                """
                SELECT rooms.id, rooms.room_code
                FROM rooms
                WHERE rooms.visibility = 'public'
                  AND rooms.status IN ('waiting', 'setup')
                  AND NOT EXISTS (
                      SELECT 1
                      FROM room_players mine
                      WHERE mine.room_id = rooms.id AND mine.user_id = ?
                  )
                  AND (
                      SELECT COUNT(*)
                      FROM room_players
                      WHERE room_players.room_id = rooms.id
                  ) < ?
                ORDER BY rooms.updated_at ASC
                LIMIT 1
                """,
                (user_id, PLAYER_LIMIT),
            ).fetchone()

            if room:
                conn.execute(
                    """
                    INSERT INTO room_players (room_id, user_id, seat_order, secret_word, joined_at)
                    VALUES (
                        ?, ?, (
                            SELECT COUNT(*) + 1
                            FROM room_players
                            WHERE room_players.room_id = ?
                        ), NULL, ?
                    )
                    """,
                    (room["id"], user_id, room["id"], now_ts()),
                )
                conn.execute(
                    "UPDATE rooms SET status = 'setup', updated_at = ? WHERE id = ?",
                    (now_ts(), room["id"]),
                )
                return room["room_code"], ""

        return self.create_room(user_id, "public"), ""

    def set_secret(self, user_id: int, room_code: str, secret_word: str) -> str:
        secret_word = normalize_word(secret_word)
        if not is_valid_word(secret_word):
            return "Choose a valid 5-letter isogram from the built-in dictionary."

        with self.lock, self._connect() as conn:
            room = self._get_room_for_user(conn, room_code, user_id)
            if not room:
                return "Room or player was not found."
            existing_secret = conn.execute(
                "SELECT secret_word FROM room_players WHERE room_id = ? AND user_id = ?",
                (room["id"], user_id),
            ).fetchone()["secret_word"]
            if existing_secret and room["status"] in {"setup", "playing", "finished"}:
                return "Your secret is already locked for this round."
            if room["status"] == "playing":
                return "You cannot change secrets after the round starts."

            conn.execute(
                "UPDATE room_players SET secret_word = ? WHERE room_id = ? AND user_id = ?",
                (secret_word, room["id"], user_id),
            )
            self._refresh_room_phase(conn, room["id"])
            return ""

    def submit_guess(self, user_id: int, room_code: str, guess_word: str) -> str:
        guess_word = normalize_word(guess_word)
        if not is_valid_word(guess_word):
            return "Guesses must be valid 5-letter isograms from the built-in dictionary."

        with self.lock, self._connect() as conn:
            room = self._get_room_for_user(conn, room_code, user_id)
            if not room:
                return "Room or player was not found."
            if room["status"] != "playing":
                return "The round has not started yet."
            if room["current_turn_user_id"] != user_id:
                return "It is not your turn yet."

            players = conn.execute(
                """
                SELECT room_players.user_id, room_players.secret_word, users.username
                FROM room_players
                JOIN users ON users.id = room_players.user_id
                WHERE room_players.room_id = ?
                ORDER BY room_players.seat_order ASC
                """,
                (room["id"],),
            ).fetchall()
            if len(players) != PLAYER_LIMIT:
                return "Waiting for another player to join."

            opponent = next(player for player in players if player["user_id"] != user_id)
            if not opponent["secret_word"]:
                return "Your opponent has not locked in a word yet."

            duplicate = conn.execute(
                """
                SELECT 1
                FROM guesses
                WHERE room_id = ? AND round_number = ? AND user_id = ? AND guess_word = ?
                """,
                (room["id"], room["round_number"], user_id, guess_word),
            ).fetchone()
            if duplicate:
                return "You already used that guess this round."

            score = common_letter_score(guess_word, opponent["secret_word"])
            conn.execute(
                """
                INSERT INTO guesses (room_id, round_number, user_id, guess_word, score, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (room["id"], room["round_number"], user_id, guess_word, score, now_ts()),
            )

            if guess_word == opponent["secret_word"]:
                finished_at = now_ts()
                winner_secret = next(player["secret_word"] for player in players if player["user_id"] == user_id)
                losing_word = opponent["secret_word"]
                conn.execute(
                    """
                    UPDATE rooms
                    SET status = 'finished',
                        current_turn_user_id = NULL,
                        winner_user_id = ?,
                        winning_word = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (user_id, winner_secret, finished_at, room["id"]),
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO round_results (
                        room_id, round_number, winner_user_id, winning_word, losing_word, finished_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (room["id"], room["round_number"], user_id, winner_secret, losing_word, finished_at),
                )
            else:
                conn.execute(
                    "UPDATE rooms SET current_turn_user_id = ?, updated_at = ? WHERE id = ?",
                    (opponent["user_id"], now_ts(), room["id"]),
                )
            return ""

    def restart_room(self, user_id: int, room_code: str) -> str:
        with self.lock, self._connect() as conn:
            room = self._get_room_for_user(conn, room_code, user_id)
            if not room:
                return "Room not found."

            conn.execute("UPDATE room_players SET secret_word = NULL WHERE room_id = ?", (room["id"],))
            conn.execute(
                """
                UPDATE rooms
                SET status = CASE
                        WHEN (SELECT COUNT(*) FROM room_players WHERE room_players.room_id = rooms.id) = 2
                        THEN 'setup'
                        ELSE 'waiting'
                    END,
                    round_number = round_number + 1,
                    current_turn_user_id = NULL,
                    winner_user_id = NULL,
                    winning_word = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (now_ts(), room["id"]),
            )
            return ""

    def close_room(self, user_id: int, room_code: str) -> str:
        with self.lock, self._connect() as conn:
            room = self._get_room_for_user(conn, room_code, user_id)
            if not room:
                return "Room not found."
            if room["status"] in {"setup", "playing"}:
                return "Only inactive games can be closed."

            conn.execute(
                """
                UPDATE rooms
                SET status = 'closed',
                    current_turn_user_id = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (now_ts(), room["id"]),
            )
            return ""

    def reopen_room(self, user_id: int, room_code: str) -> str:
        with self.lock, self._connect() as conn:
            room = self._get_room_for_user(conn, room_code, user_id)
            if not room:
                return "Room not found."
            if room["status"] != "closed":
                return "That game is already open."

            players = conn.execute(
                """
                SELECT user_id, seat_order, secret_word
                FROM room_players
                WHERE room_id = ?
                ORDER BY seat_order ASC
                """,
                (room["id"],),
            ).fetchall()
            player_count = len(players)

            if room["winner_user_id"]:
                conn.execute(
                    """
                    UPDATE rooms
                    SET status = 'finished',
                        current_turn_user_id = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now_ts(), room["id"]),
                )
                return ""

            if player_count < PLAYER_LIMIT:
                conn.execute(
                    """
                    UPDATE rooms
                    SET status = 'waiting',
                        current_turn_user_id = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now_ts(), room["id"]),
                )
                return ""

            if all(player["secret_word"] for player in players):
                guess_count = conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM guesses
                    WHERE room_id = ? AND round_number = ?
                    """,
                    (room["id"], room["round_number"]),
                ).fetchone()["count"]
                starter_index = (room["round_number"] - 1) % PLAYER_LIMIT
                current_index = (starter_index + guess_count) % PLAYER_LIMIT
                conn.execute(
                    """
                    UPDATE rooms
                    SET status = 'playing',
                        current_turn_user_id = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (players[current_index]["user_id"], now_ts(), room["id"]),
                )
                return ""

            conn.execute(
                """
                UPDATE rooms
                SET status = 'setup',
                    current_turn_user_id = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (now_ts(), room["id"]),
            )
            return ""

    def get_bootstrap(self, user_id: Optional[int], room_code: str = "") -> dict:
        payload = {
            "user": None,
            "lobby": None,
            "invite": None,
            "room": None,
            "words_count": len(WORD_LIST),
        }

        if user_id:
            with self._connect() as conn:
                user = conn.execute("SELECT id, username, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
                payload["user"] = dict(user) if user else None
                if user:
                    payload["lobby"] = self._lobby_state(conn, user_id)

        if room_code:
            room_code = room_code.strip().upper()
            with self._connect() as conn:
                payload["invite"] = self._room_summary(conn, room_code, user_id)
                if user_id:
                    participant = conn.execute(
                        """
                        SELECT 1
                        FROM rooms
                        JOIN room_players ON room_players.room_id = rooms.id
                        WHERE rooms.room_code = ? AND room_players.user_id = ?
                        """,
                        (room_code, user_id),
                    ).fetchone()
                    if participant:
                        payload["room"] = self._room_state(conn, room_code, user_id)

        return payload

    def room_state(self, user_id: int, room_code: str) -> tuple[Optional[dict], str]:
        with self._connect() as conn:
            state = self._room_state(conn, room_code, user_id)
            if not state:
                return None, "Room or player was not found."
            return state, ""

    def _lobby_state(self, conn: sqlite3.Connection, user_id: int) -> dict:
        rows = conn.execute(
            """
            SELECT
                rooms.room_code,
                rooms.visibility,
                rooms.status,
                rooms.round_number,
                rooms.updated_at,
                rooms.winning_word,
                me.secret_word AS my_secret,
                winner.username AS winner_name,
                (
                    SELECT users.username
                    FROM room_players opp
                    JOIN users ON users.id = opp.user_id
                    WHERE opp.room_id = rooms.id AND opp.user_id != ?
                    ORDER BY opp.seat_order ASC
                    LIMIT 1
                ) AS opponent_name
            FROM rooms
            JOIN room_players me ON me.room_id = rooms.id
            LEFT JOIN users winner ON winner.id = rooms.winner_user_id
            WHERE me.user_id = ?
              AND rooms.status != 'closed'
            ORDER BY rooms.updated_at DESC
            LIMIT 12
            """,
            (user_id, user_id),
        ).fetchall()

        stats = conn.execute(
            """
            SELECT
                SUM(CASE WHEN round_results.winner_user_id = ? THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN round_results.winner_user_id IS NOT NULL AND round_results.winner_user_id != ? THEN 1 ELSE 0 END) AS losses,
                COUNT(*) AS finished_games
            FROM round_results
            JOIN rooms ON rooms.id = round_results.room_id
            WHERE EXISTS (
                SELECT 1
                FROM room_players
                WHERE room_players.room_id = rooms.id AND room_players.user_id = ?
            )
            """,
            (user_id, user_id, user_id),
        ).fetchone()

        waiting_public = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM rooms
            WHERE visibility = 'public'
              AND status IN ('waiting', 'setup')
              AND (
                  SELECT COUNT(*)
                  FROM room_players
                  WHERE room_players.room_id = rooms.id
              ) < ?
            """,
            (PLAYER_LIMIT,),
        ).fetchone()["count"]

        return {
            "stats": {
                "wins": stats["wins"] or 0,
                "losses": stats["losses"] or 0,
                "finished_games": stats["finished_games"] or 0,
            },
            "public_waiting_count": waiting_public,
            "rooms": [
                {
                    "room_code": row["room_code"],
                    "visibility": row["visibility"],
                    "status": row["status"],
                    "round_number": row["round_number"],
                    "updated_at": row["updated_at"],
                    "winner_name": row["winner_name"],
                    "winning_word": row["winning_word"],
                    "opponent_name": row["opponent_name"],
                }
                for row in rows
            ],
        }

    def _room_summary(self, conn: sqlite3.Connection, room_code: str, user_id: Optional[int]) -> Optional[dict]:
        room = conn.execute(
            "SELECT id, room_code, visibility, status, round_number FROM rooms WHERE room_code = ?",
            (room_code,),
        ).fetchone()
        if not room:
            return None

        players = conn.execute(
            """
            SELECT users.username
            FROM room_players
            JOIN users ON users.id = room_players.user_id
            WHERE room_players.room_id = ?
            ORDER BY room_players.seat_order ASC
            """,
            (room["id"],),
        ).fetchall()
        is_participant = False
        if user_id:
            is_participant = bool(
                conn.execute(
                    "SELECT 1 FROM room_players WHERE room_id = ? AND user_id = ?",
                    (room["id"], user_id),
                ).fetchone()
            )

        return {
            "room_code": room["room_code"],
            "visibility": room["visibility"],
            "status": room["status"],
            "round_number": room["round_number"],
            "players": [row["username"] for row in players],
            "player_count": len(players),
            "is_participant": is_participant,
            "can_join": len(players) < PLAYER_LIMIT and not is_participant,
        }

    def _room_state(self, conn: sqlite3.Connection, room_code: str, user_id: int) -> Optional[dict]:
        room = self._get_room_for_user(conn, room_code, user_id)
        if not room:
            return None

        players = conn.execute(
            """
            SELECT room_players.user_id, room_players.seat_order, room_players.secret_word, users.username
            FROM room_players
            JOIN users ON users.id = room_players.user_id
            WHERE room_players.room_id = ?
            ORDER BY room_players.seat_order ASC
            """,
            (room["id"],),
        ).fetchall()
        guesses = conn.execute(
            """
            SELECT guesses.guess_word, guesses.score, guesses.user_id, users.username
            FROM guesses
            JOIN users ON users.id = guesses.user_id
            WHERE guesses.room_id = ? AND guesses.round_number = ?
            ORDER BY guesses.created_at ASC, guesses.id ASC
            """,
            (room["id"], room["round_number"]),
        ).fetchall()
        round_history = conn.execute(
            """
            SELECT
                all_rounds.round_number,
                COALESCE(guess_counts.guess_count, 0) AS guess_count,
                users.username AS winner_name,
                round_results.winning_word,
                round_results.losing_word
            FROM (
                SELECT DISTINCT round_number
                FROM guesses
                WHERE room_id = ?
                UNION
                SELECT round_number
                FROM round_results
                WHERE room_id = ?
            ) AS all_rounds
            LEFT JOIN (
                SELECT round_number, COUNT(*) AS guess_count
                FROM guesses
                WHERE room_id = ?
                GROUP BY round_number
            ) AS guess_counts ON guess_counts.round_number = all_rounds.round_number
            LEFT JOIN round_results ON round_results.room_id = ? AND round_results.round_number = all_rounds.round_number
            LEFT JOIN users ON users.id = round_results.winner_user_id
            ORDER BY all_rounds.round_number DESC
            LIMIT 5
            """,
            (room["id"], room["id"], room["id"], room["id"]),
        ).fetchall()
        current_turn_name = None
        if room["current_turn_user_id"]:
            current_turn = next(player for player in players if player["user_id"] == room["current_turn_user_id"])
            current_turn_name = current_turn["username"]

        me = next(player for player in players if player["user_id"] == user_id)
        opponent = next((player for player in players if player["user_id"] != user_id), None)
        winner_name = None
        if room["winner_user_id"]:
            winner_name = next(player["username"] for player in players if player["user_id"] == room["winner_user_id"])

        return {
            "room_code": room["room_code"],
            "visibility": room["visibility"],
            "status": room["status"],
            "round_number": room["round_number"],
            "players": [
                {
                    "username": player["username"],
                    "is_you": player["user_id"] == user_id,
                    "has_secret": bool(player["secret_word"]),
                    "seat_order": player["seat_order"],
                }
                for player in players
            ],
            "guesses": [
                {
                    "player_name": guess["username"],
                    "guess": guess["guess_word"],
                    "score": guess["score"],
                    "is_exact": guess["score"] == 5 and opponent and guess["guess_word"] == (opponent["secret_word"] if guess["user_id"] == user_id else me["secret_word"]),
                }
                for guess in guesses
            ],
            "winner_name": winner_name,
            "winning_word": room["winning_word"],
            "is_your_turn": room["current_turn_user_id"] == user_id,
            "current_turn_name": current_turn_name,
            "my_name": me["username"],
            "my_secret_set": bool(me["secret_word"]),
            "my_secret_word": me["secret_word"],
            "revealed_opponent_word": opponent["secret_word"] if room["status"] == "finished" and opponent else None,
            "opponent_ready": bool(opponent and opponent["secret_word"]),
            "opponent_name": opponent["username"] if opponent else None,
            "can_restart": len(players) == PLAYER_LIMIT,
            "round_history": [
                {
                    "round_number": entry["round_number"],
                    "guess_count": entry["guess_count"],
                    "winner_name": entry["winner_name"],
                    "winning_word": entry["winning_word"],
                    "losing_word": entry["losing_word"],
                }
                for entry in round_history
            ],
        }

    def _get_room_for_user(self, conn: sqlite3.Connection, room_code: str, user_id: int) -> Optional[sqlite3.Row]:
        return conn.execute(
            """
            SELECT rooms.*
            FROM rooms
            JOIN room_players ON room_players.room_id = rooms.id
            WHERE rooms.room_code = ? AND room_players.user_id = ?
            """,
            (room_code.strip().upper(), user_id),
        ).fetchone()

    def _refresh_room_phase(self, conn: sqlite3.Connection, room_id: int):
        room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
        players = conn.execute(
            """
            SELECT user_id, seat_order, secret_word
            FROM room_players
            WHERE room_id = ?
            ORDER BY seat_order ASC
            """,
            (room_id,),
        ).fetchall()
        player_count = len(players)
        ready_players = [player for player in players if player["secret_word"]]

        if player_count < PLAYER_LIMIT:
            conn.execute(
                """
                UPDATE rooms
                SET status = 'waiting',
                    current_turn_user_id = NULL,
                    winner_user_id = NULL,
                    winning_word = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (now_ts(), room_id),
            )
            return

        if len(ready_players) == PLAYER_LIMIT:
            starter_index = (room["round_number"] - 1) % PLAYER_LIMIT
            starter_user_id = players[starter_index]["user_id"]
            conn.execute(
                """
                UPDATE rooms
                SET status = 'playing',
                    current_turn_user_id = ?,
                    winner_user_id = NULL,
                    winning_word = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (starter_user_id, now_ts(), room_id),
            )
            return

        conn.execute(
            """
            UPDATE rooms
            SET status = 'setup',
                current_turn_user_id = NULL,
                winner_user_id = NULL,
                winning_word = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (now_ts(), room_id),
        )


STORE = GameStore(DB_PATH)


class JottoHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._serve_static("index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/app.js":
            self._serve_static("app.js", "application/javascript; charset=utf-8")
            return
        if parsed.path == "/styles.css":
            self._serve_static("styles.css", "text/css; charset=utf-8")
            return
        if parsed.path == "/api/bootstrap":
            room_code = (parse_qs(parsed.query).get("room") or [""])[0]
            user = self._current_user()
            payload = STORE.get_bootstrap(user["id"] if user else None, room_code)
            self._json_response(payload)
            return
        if parsed.path == "/api/words":
            self._json_response({"words": WORD_LIST})
            return
        if parsed.path == "/api/room-state":
            user = self._require_user()
            if not user:
                return
            room_code = (parse_qs(parsed.query).get("room") or [""])[0]
            payload, error = STORE.room_state(user["id"], room_code)
            if error:
                self._json_response({"error": error}, status=HTTPStatus.BAD_REQUEST)
                return
            self._json_response(payload)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        parsed = urlparse(self.path)
        payload = parse_json_body(self)
        if payload is None:
            self._json_response({"error": "Invalid JSON body."}, status=HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/signup":
            session, error = STORE.create_user(clean_name(payload.get("username")), str(payload.get("password", "")))
            if error:
                self._json_response({"error": error}, status=HTTPStatus.BAD_REQUEST)
                return
            self._json_response({"user": session["user"]}, set_session=session["session_token"])
            return

        if parsed.path == "/api/login":
            session, error = STORE.authenticate_user(clean_name(payload.get("username")), str(payload.get("password", "")))
            if error:
                self._json_response({"error": error}, status=HTTPStatus.BAD_REQUEST)
                return
            self._json_response({"user": session["user"]}, set_session=session["session_token"])
            return

        if parsed.path == "/api/logout":
            STORE.delete_session(self._session_token())
            self._json_response({"ok": True}, clear_session=True)
            return

        user = self._require_user()
        if not user:
            return

        if parsed.path == "/api/private-room":
            room_code = STORE.create_room(user["id"], "private")
            self._json_response({"room_code": room_code})
            return

        if parsed.path == "/api/matchmaking":
            room_code, error = STORE.find_or_create_match(user["id"])
            if error:
                self._json_response({"error": error}, status=HTTPStatus.BAD_REQUEST)
                return
            self._json_response({"room_code": room_code})
            return

        if parsed.path == "/api/join-room":
            room_code, error = STORE.join_room(user["id"], str(payload.get("room_code", "")))
            if error:
                self._json_response({"error": error}, status=HTTPStatus.BAD_REQUEST)
                return
            self._json_response({"room_code": room_code})
            return

        if parsed.path == "/api/set-secret":
            error = STORE.set_secret(user["id"], str(payload.get("room_code", "")), str(payload.get("secret", "")))
            if error:
                self._json_response({"error": error}, status=HTTPStatus.BAD_REQUEST)
                return
            self._json_response({"ok": True})
            return

        if parsed.path == "/api/guess":
            error = STORE.submit_guess(user["id"], str(payload.get("room_code", "")), str(payload.get("guess", "")))
            if error:
                self._json_response({"error": error}, status=HTTPStatus.BAD_REQUEST)
                return
            self._json_response({"ok": True})
            return

        if parsed.path == "/api/restart":
            error = STORE.restart_room(user["id"], str(payload.get("room_code", "")))
            if error:
                self._json_response({"error": error}, status=HTTPStatus.BAD_REQUEST)
                return
            self._json_response({"ok": True})
            return

        if parsed.path == "/api/close-room":
            error = STORE.close_room(user["id"], str(payload.get("room_code", "")))
            if error:
                self._json_response({"error": error}, status=HTTPStatus.BAD_REQUEST)
                return
            self._json_response({"ok": True})
            return

        if parsed.path == "/api/reopen-room":
            error = STORE.reopen_room(user["id"], str(payload.get("room_code", "")))
            if error:
                self._json_response({"error": error}, status=HTTPStatus.BAD_REQUEST)
                return
            self._json_response({"ok": True})
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, _format, *args):
        return

    def _serve_static(self, filename: str, content_type: str):
        file_path = STATIC_DIR / filename
        if not file_path.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_response(self, payload: dict, status: int = HTTPStatus.OK, set_session: Optional[str] = None, clear_session: bool = False):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if set_session:
            self.send_header(
                "Set-Cookie",
                f"{SESSION_COOKIE}={set_session}; Max-Age={SESSION_MAX_AGE}; Path=/; HttpOnly; SameSite=Lax",
            )
        if clear_session:
            self.send_header(
                "Set-Cookie",
                f"{SESSION_COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax",
            )
        self.end_headers()
        self.wfile.write(body)

    def _session_token(self) -> str:
        cookie = SimpleCookie(self.headers.get("Cookie"))
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel else ""

    def _current_user(self) -> Optional[dict]:
        return STORE.get_user_by_session(self._session_token())

    def _require_user(self) -> Optional[dict]:
        user = self._current_user()
        if not user:
            self._json_response({"error": "Please log in first."}, status=HTTPStatus.UNAUTHORIZED, clear_session=True)
            return None
        return user


def main():
    if not WORD_LIST:
        raise RuntimeError("No valid 5-letter isogram words were loaded from data/words.txt")
    server = ThreadingHTTPServer((HOST, PORT), JottoHandler)
    print(f"Serving Jotto Duel on http://localhost:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()

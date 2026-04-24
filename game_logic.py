"""
Pure game logic for Imposter Media.

Framework-free by design. Every function takes its inputs explicitly and
mutates the `Room` you pass in — no hidden globals, no HTTP, no WebSockets.
That makes every transition directly testable:

    room = create_room("Alice")
    add_player(room, "Bob"); add_player(room, "Cara")
    start_round(room, ContentPair("apple", "orange"), imposter_id=bob.id)
    end_showing_content(room)
    submit_word(room, alice.id, "fruit")
    ...

The web layer (`app.py`) is a thin adapter: map HTTP -> function call,
broadcast the resulting state over WebSocket, translate `GameError` -> 400.

-----------------------------------------------------------------------------
States
-----------------------------------------------------------------------------
    waiting_lobby       Players are joining. Nobody's playing yet.
    showing_content     Each player is privately viewing their content.
    submitting_words    Each player is typing their one-word answer.
    revealing_words     All words are public.
    discussion          Timed group discussion before voting.
    voting              Each player votes for the suspected imposter.
    revealing_results   Round over. Imposter and outcome revealed.
                        Imposter may submit a guess for the normal content.
    scoreboard          Cumulative scores shown between rounds and at game end.

Transitions
    waiting_lobby      -> showing_content       start_round
    showing_content    -> submitting_words      end_showing_content
    submitting_words   -> revealing_words       reveal_words
    revealing_words    -> discussion            start_discussion
    discussion         -> voting                start_voting
    voting             -> revealing_results     calculate_results
    revealing_results  -> scoreboard            show_scoreboard
    scoreboard         -> showing_content       next_round (if rounds left)
    any                -> waiting_lobby         reset_game

Backwards-compatibility note: `start_voting` still accepts REVEALING_WORDS
as a "skip discussion" shortcut so existing callers/tests keep working.
"""

from __future__ import annotations

import random
import re
import string
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


# =============================================================================
# Tunables
# =============================================================================

MIN_PLAYERS = 3
MAX_PLAYERS = 10

# Default phase durations (seconds). Each room's settings can override these.
SHOWING_CONTENT_SECONDS = 15
SUBMITTING_WORDS_SECONDS = 25
DISCUSSION_SECONDS = 30
VOTING_SECONDS = 30

# Scoring.
POINTS_GROUP_WIN = 2          # awarded to each non-imposter when group catches imposter
POINTS_IMPOSTER_WIN = 3       # awarded to the imposter when they survive
POINTS_IMPOSTER_GUESS = 2     # bonus if imposter correctly guesses normal content

_MAX_NAME_LEN = 20
_MAX_WORD_LEN = 30
_MAX_GUESS_LEN = 60

_ROOM_CODE_LEN = 5
# Avoid ambiguous characters (I, O, 0, 1) so codes are easy to read aloud.
_ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


# =============================================================================
# States
# =============================================================================

class GameState(str, Enum):
    WAITING_LOBBY = "waiting_lobby"
    SHOWING_CONTENT = "showing_content"
    SUBMITTING_WORDS = "submitting_words"
    REVEALING_WORDS = "revealing_words"
    DISCUSSION = "discussion"
    VOTING = "voting"
    REVEALING_RESULTS = "revealing_results"
    SCOREBOARD = "scoreboard"


class ContentCategory(str, Enum):
    """Tag each content pair so rooms can limit picks to a chosen theme."""
    INFLUENCERS = "influencers"
    SPORTS = "sports"
    MUSIC = "music"
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"
    MEMES = "memes"
    MOVIES = "movies"
    RANDOM_OBJECTS = "random_objects"
    PICTURES = "pictures"
    VIDEOS = "videos"


ALL_CATEGORIES: List[str] = [c.value for c in ContentCategory]


# =============================================================================
# Data classes
# =============================================================================

@dataclass
class ContentPair:
    """One entry in a content bank.

    `normal` goes to the group; `imposter` goes to the one imposter. They
    should be thematically adjacent — close enough that the imposter can
    plausibly bluff with a one-word answer, but different enough that a
    careful group can catch the tell.
    """
    normal: str
    imposter: str
    kind: str = "text"                      # "text" | "image" | "video"
    category: str = "random_objects"        # see ContentCategory


@dataclass
class Vote:
    """A single, immutable cast vote. First vote wins (no changing minds)."""
    voter_id: str
    target_id: str


@dataclass
class Player:
    id: str
    name: str
    is_host: bool = False
    connected: bool = True

    # Per-round state. `_reset_player_round_state()` wipes it between rounds
    # so nothing leaks forward.
    private_content: Optional[str] = None
    content_kind: Optional[str] = None
    is_imposter: bool = False
    word: Optional[str] = None
    imposter_guess: Optional[str] = None    # only the imposter fills this in

    # Cumulative across the whole game. Reset by reset_game().
    score: int = 0


@dataclass
class Round:
    number: int
    content_pair: ContentPair
    imposter_id: str
    votes: List[Vote] = field(default_factory=list)
    result: Optional["RoundResult"] = None


@dataclass
class RoundResult:
    """Everything a client needs to render the results screen."""
    round_number: int
    imposter_id: str
    imposter_name: str
    vote_counts: Dict[str, int]              # name -> vote count
    words: Dict[str, Optional[str]]          # name -> word
    normal_content: str
    imposter_content: str
    content_kind: str
    content_category: str                    # the theme of this round
    group_won: bool                          # outright majority on the imposter
    imposter_won: bool                       # not group_won (covers ties)
    tied: bool                               # >1 player at the top
    accused_names: List[str]                 # every player tied for most votes
    # Scoring for THIS round only. `name -> delta`.
    score_delta: Dict[str, int] = field(default_factory=dict)
    # Imposter's optional guess at the normal content.
    imposter_guess: Optional[str] = None
    imposter_guess_correct: Optional[bool] = None


@dataclass
class RoomSettings:
    """Per-room configuration the host can tweak from the lobby.

    Defaults produce a reasonable 5-round game with a random category mix.
    Every field is optional from the host's perspective: if the client omits
    a setting, keep the current value.
    """
    total_rounds: int = 5
    # None = draw from the whole bank; otherwise one of ContentCategory values.
    category: Optional[str] = None
    max_players: int = MAX_PLAYERS
    number_of_imposters: int = 1             # reserved; clamped to 1 today
    # Phase durations in seconds.
    showing_content_seconds: int = SHOWING_CONTENT_SECONDS
    word_submission_seconds: int = SUBMITTING_WORDS_SECONDS
    discussion_seconds: int = DISCUSSION_SECONDS
    voting_seconds: int = VOTING_SECONDS


@dataclass
class Room:
    code: str
    host_id: str
    players: Dict[str, Player] = field(default_factory=dict)
    state: GameState = GameState.WAITING_LOBBY
    phase_ends_at: Optional[float] = None
    current_round: Optional[Round] = None
    rounds_played: int = 0
    created_at: float = field(default_factory=time.time)
    settings: RoomSettings = field(default_factory=RoomSettings)


# =============================================================================
# Errors
# =============================================================================

class GameError(Exception):
    """Rule violation. The web layer maps these to HTTP 400 with the message."""


# =============================================================================
# Private helpers
# =============================================================================

def _generate_room_code(existing: set) -> str:
    while True:
        code = "".join(random.choices(_ROOM_CODE_ALPHABET, k=_ROOM_CODE_LEN))
        if code not in existing:
            return code


def _generate_player_id() -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=16))


def _reset_player_round_state(player: Player) -> None:
    """Wipe per-round fields. Does NOT touch cumulative score."""
    player.private_content = None
    player.content_kind = None
    player.is_imposter = False
    player.word = None
    player.imposter_guess = None


def _active_players(room: Room) -> List[Player]:
    """Players still connected. Disconnected players don't block progression."""
    return [p for p in room.players.values() if p.connected]


def _normalize_for_guess(s: str) -> str:
    """Lowercase + strip punctuation so the imposter's guess matches loosely."""
    s = (s or "").lower().strip()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _guess_matches_normal(guess: str, normal: str) -> bool:
    """Did the imposter's guess identify the normal content?

    Both strings are normalized first (lowercase, no punctuation). We count
    a match if ANY of these hold:
      - one is a substring of the other ("pizza" vs "pepperoni pizza")
      - every word in the guess appears somewhere in the target
        ("LeBron dunking" vs "LeBron James dunking")

    URLs won't match except by accident — there's nothing to guess against.
    """
    g = _normalize_for_guess(guess)
    n = _normalize_for_guess(normal)
    if not g or not n:
        return False
    if g == n or g in n or n in g:
        return True
    g_words = set(g.split())
    n_words = set(n.split())
    return bool(g_words) and g_words.issubset(n_words)


# =============================================================================
# Public API
# =============================================================================

def create_room(host_name: str, *, existing_codes: Optional[set] = None) -> Room:
    """Create a new room with the caller as the first player (and host).

    `existing_codes` is an opt-in uniqueness guard. Pass a set of codes
    you've already handed out (e.g. from a registry) to guarantee the
    generated code doesn't collide with one already in use.
    """
    code = _generate_room_code(existing_codes or set())
    room = Room(code=code, host_id="")
    add_player(room, host_name)  # The first player is promoted to host.
    return room


def add_player(room: Room, name: str) -> Player:
    """Add a player to the lobby.

    Raises `GameError` if:
      - the name is empty after trimming
      - the game is already in progress (not WAITING_LOBBY)
      - the room is at its configured max_players cap
    """
    cleaned_name = (name or "").strip()[:_MAX_NAME_LEN]
    if not cleaned_name:
        raise GameError("Name cannot be empty")
    if room.state != GameState.WAITING_LOBBY:
        raise GameError("Cannot join — game already in progress")
    cap = min(room.settings.max_players, MAX_PLAYERS)
    if len(room.players) >= cap:
        raise GameError(f"Room is full (max {cap})")

    player = Player(id=_generate_player_id(), name=cleaned_name)
    if not room.players:
        player.is_host = True
        room.host_id = player.id
    room.players[player.id] = player
    return player


def remove_player(room: Room, player_id: str) -> None:
    """Remove a player.

    - In WAITING_LOBBY: delete cleanly; if the host left, promote the next player.
    - Mid-game: mark as disconnected. Disconnected players are filtered out of
      `all_words_submitted` and `all_votes_submitted` so one dropout doesn't
      stall the whole room.
    """
    if player_id not in room.players:
        return
    if room.state == GameState.WAITING_LOBBY:
        was_host = room.players[player_id].is_host
        del room.players[player_id]
        if was_host and room.players:
            new_host = next(iter(room.players.values()))
            new_host.is_host = True
            room.host_id = new_host.id
    else:
        room.players[player_id].connected = False


# ------------------------------------------------------------------
# Room settings
# ------------------------------------------------------------------

def update_settings(room: Room, patch: dict) -> RoomSettings:
    """Apply a partial settings update. Only valid while in the lobby.

    `patch` is a mapping of field -> value. Unknown keys are ignored; invalid
    values raise `GameError`. Returns the current settings after the patch.
    """
    if room.state != GameState.WAITING_LOBBY:
        raise GameError("Settings can only be changed in the lobby")

    s = room.settings

    if "total_rounds" in patch:
        n = _as_int(patch["total_rounds"], "total_rounds")
        if n < 1 or n > 20:
            raise GameError("total_rounds must be between 1 and 20")
        s.total_rounds = n

    if "category" in patch:
        c = patch["category"]
        if c in (None, "", "any"):
            s.category = None
        elif c in ALL_CATEGORIES:
            s.category = c
        else:
            raise GameError(f"Unknown category: {c}")

    if "max_players" in patch:
        n = _as_int(patch["max_players"], "max_players")
        if n < MIN_PLAYERS or n > MAX_PLAYERS:
            raise GameError(
                f"max_players must be between {MIN_PLAYERS} and {MAX_PLAYERS}"
            )
        if n < len(room.players):
            raise GameError("max_players can't be lower than the number of players already in the room")
        s.max_players = n

    if "number_of_imposters" in patch:
        n = _as_int(patch["number_of_imposters"], "number_of_imposters")
        # Reserved for future use — the rest of the code assumes 1 imposter.
        if n != 1:
            raise GameError("number_of_imposters must be 1 for now")
        s.number_of_imposters = n

    for timer_key in ("showing_content_seconds", "word_submission_seconds",
                      "discussion_seconds", "voting_seconds"):
        if timer_key in patch:
            n = _as_int(patch[timer_key], timer_key)
            if n < 5 or n > 300:
                raise GameError(f"{timer_key} must be between 5 and 300 seconds")
            setattr(s, timer_key, n)

    return s


def _as_int(v, field_name: str) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        raise GameError(f"{field_name} must be a number")


# ------------------------------------------------------------------
# Round lifecycle
# ------------------------------------------------------------------

def start_round(
    room: Room,
    content_pair: ContentPair,
    imposter_id: Optional[str] = None,
) -> Round:
    """Start a new round.

    Picks a random imposter from connected players if `imposter_id` is None.
    Assigns content privately and transitions to SHOWING_CONTENT.

    Raises `GameError` if:
      - the room isn't in WAITING_LOBBY, REVEALING_RESULTS, or SCOREBOARD
      - there are fewer than MIN_PLAYERS active players
      - a provided `imposter_id` doesn't belong to this room
    """
    # SCOREBOARD is the normal "ready for next round" spot; REVEALING_RESULTS
    # is kept as an accepted source for backwards compatibility with older
    # callers (and the test suite).
    if room.state not in (GameState.WAITING_LOBBY,
                          GameState.REVEALING_RESULTS,
                          GameState.SCOREBOARD):
        raise GameError("Cannot start a round from this state")

    active = _active_players(room)
    if len(active) < MIN_PLAYERS:
        raise GameError(f"Need at least {MIN_PLAYERS} active players to start")

    if imposter_id is None:
        imposter_id = random.choice(active).id
    if imposter_id not in room.players:
        raise GameError("Imposter must be a player in the room")

    room.rounds_played += 1
    round_ = Round(
        number=room.rounds_played,
        content_pair=content_pair,
        imposter_id=imposter_id,
    )
    room.current_round = round_
    assign_content(room, content_pair)
    room.state = GameState.SHOWING_CONTENT
    room.phase_ends_at = time.time() + room.settings.showing_content_seconds
    return round_


def assign_content(room: Room, content_pair: ContentPair) -> None:
    """Assign each player their private content for the current round.

    Everyone gets `content_pair.normal`, except the one imposter (determined
    by `room.current_round.imposter_id`), who gets `content_pair.imposter`.

    Called automatically by `start_round`; exposed separately for tests and
    custom flows (e.g. re-rolling the content mid-round for debugging).
    """
    if not room.current_round:
        raise GameError("No active round to assign content to")
    imposter_id = room.current_round.imposter_id
    for p in room.players.values():
        _reset_player_round_state(p)
        p.is_imposter = (p.id == imposter_id)
        p.content_kind = content_pair.kind
        p.private_content = (content_pair.imposter
                             if p.is_imposter
                             else content_pair.normal)


def end_showing_content(room: Room) -> None:
    """End the viewing phase (normally called by the web layer's timer).

    Transitions SHOWING_CONTENT -> SUBMITTING_WORDS and wipes private content
    off every player so it can't be replayed from a cached state snapshot.
    """
    if room.state != GameState.SHOWING_CONTENT:
        return
    for p in room.players.values():
        p.private_content = None
    room.state = GameState.SUBMITTING_WORDS
    room.phase_ends_at = time.time() + room.settings.word_submission_seconds


# ------------------------------------------------------------------
# Word submission
# ------------------------------------------------------------------

def submit_word(room: Room, player_id: str, word: str) -> None:
    """Record a player's one-word answer.

    Enforces, in order:
      1. The game is in SUBMITTING_WORDS.
      2. The player is a member of the room.
      3. The player has not already submitted a word (one-shot only).
      4. The word is non-empty after trimming.
      5. The word is exactly one whitespace-separated token.

    The accepted word is lowercased and truncated to `_MAX_WORD_LEN`.
    """
    if room.state != GameState.SUBMITTING_WORDS:
        raise GameError("Not accepting words right now")
    if player_id not in room.players:
        raise GameError("Unknown player")

    player = room.players[player_id]
    if player.word is not None:
        raise GameError("Your word is already locked in")

    cleaned = (word or "").strip()
    if not cleaned:
        raise GameError("Word cannot be empty")
    if len(cleaned.split()) > 1:
        raise GameError("Only one word allowed")

    player.word = cleaned[:_MAX_WORD_LEN].lower()


def all_words_submitted(room: Room) -> bool:
    """True when every connected player has submitted a word."""
    return all(p.word for p in _active_players(room))


def reveal_words(room: Room) -> Dict[str, Optional[str]]:
    """Transition SUBMITTING_WORDS -> REVEALING_WORDS and return the words.

    Also safe to call while already in REVEALING_WORDS — returns the current
    words without changing state. Any other state raises.
    """
    if room.state == GameState.SUBMITTING_WORDS:
        room.state = GameState.REVEALING_WORDS
        room.phase_ends_at = None  # Untimed; host decides when to start discussion.
    elif room.state != GameState.REVEALING_WORDS:
        raise GameError("Not in the word-reveal phase")
    return {p.name: p.word for p in room.players.values()}


# ------------------------------------------------------------------
# Discussion / Voting
# ------------------------------------------------------------------

def start_discussion(room: Room) -> None:
    """Host action: REVEALING_WORDS -> DISCUSSION (a timed talk window)."""
    if room.state != GameState.REVEALING_WORDS:
        raise GameError("Discussion can only start from the word-reveal phase")
    room.state = GameState.DISCUSSION
    room.phase_ends_at = time.time() + room.settings.discussion_seconds


def start_voting(room: Room) -> None:
    """DISCUSSION -> VOTING.

    Also accepts REVEALING_WORDS as a "skip discussion" shortcut so older
    callers / tests that go straight from reveal to vote still work.
    """
    if room.state not in (GameState.DISCUSSION, GameState.REVEALING_WORDS):
        raise GameError("Voting can only start from the word-reveal or discussion phase")
    room.state = GameState.VOTING
    room.phase_ends_at = time.time() + room.settings.voting_seconds


def submit_vote(room: Room, voter_id: str, target_id: str) -> None:
    """Cast a vote.

    Rules, enforced in order:
      1. Room must be in VOTING. (Anything else raises — you can't vote
         outside the voting phase.)
      2. Both player ids must exist in the room.
      3. A player cannot vote for themselves.
      4. A player cannot vote more than once — the first vote stands.
    """
    if room.state != GameState.VOTING:
        raise GameError("Not in the voting phase")
    if not room.current_round:
        raise GameError("No active round")
    if voter_id not in room.players or target_id not in room.players:
        raise GameError("Unknown player")
    if voter_id == target_id:
        raise GameError("You cannot vote for yourself")
    if any(v.voter_id == voter_id for v in room.current_round.votes):
        raise GameError("You've already voted this round")

    room.current_round.votes.append(Vote(voter_id=voter_id, target_id=target_id))


def all_votes_submitted(room: Room) -> bool:
    """True when every connected player has cast a vote."""
    if not room.current_round:
        return False
    voters = {v.voter_id for v in room.current_round.votes}
    return all(p.id in voters for p in _active_players(room))


def calculate_results(room: Room) -> RoundResult:
    """Tally votes, apply scoring, transition VOTING -> REVEALING_RESULTS.

    Winner rules:
      - Group wins iff exactly ONE player has the most votes AND that player
        is the imposter.
      - Anything else (someone innocent topped the vote, or there's a tie
        at the top) counts as an imposter win. Ties are surfaced explicitly
        on `RoundResult.tied` with all tied players in `accused_names`.

    Scoring:
      - Group win: each non-imposter gets POINTS_GROUP_WIN.
      - Imposter win: imposter gets POINTS_IMPOSTER_WIN.
      - Imposter's optional guess at the normal content adds an
        additional POINTS_IMPOSTER_GUESS if correct — applied later by
        `submit_imposter_guess` so the result reflects it too.
    """
    if room.state != GameState.VOTING:
        raise GameError("Can only calculate results from the voting phase")
    if not room.current_round:
        raise GameError("No active round")

    round_ = room.current_round

    counts_by_id: Dict[str, int] = {}
    for v in round_.votes:
        counts_by_id[v.target_id] = counts_by_id.get(v.target_id, 0) + 1

    top = max(counts_by_id.values()) if counts_by_id else 0
    top_ids = [pid for pid, c in counts_by_id.items() if c == top]

    group_won = (len(top_ids) == 1 and top_ids[0] == round_.imposter_id)
    tied = len(top_ids) > 1
    imposter_won = not group_won

    # Apply round scoring right away and record deltas so the client can
    # animate them on the scoreboard.
    score_delta: Dict[str, int] = {}
    if group_won:
        for p in room.players.values():
            if not p.is_imposter:
                p.score += POINTS_GROUP_WIN
                score_delta[p.name] = score_delta.get(p.name, 0) + POINTS_GROUP_WIN
    else:
        imposter = room.players[round_.imposter_id]
        imposter.score += POINTS_IMPOSTER_WIN
        score_delta[imposter.name] = score_delta.get(imposter.name, 0) + POINTS_IMPOSTER_WIN

    result = RoundResult(
        round_number=round_.number,
        imposter_id=round_.imposter_id,
        imposter_name=room.players[round_.imposter_id].name,
        vote_counts={room.players[pid].name: c for pid, c in counts_by_id.items()},
        words={p.name: p.word for p in room.players.values()},
        normal_content=round_.content_pair.normal,
        imposter_content=round_.content_pair.imposter,
        content_kind=round_.content_pair.kind,
        content_category=round_.content_pair.category,
        group_won=group_won,
        imposter_won=imposter_won,
        tied=tied,
        accused_names=[room.players[pid].name for pid in top_ids],
        score_delta=score_delta,
    )
    round_.result = result
    room.state = GameState.REVEALING_RESULTS
    room.phase_ends_at = None
    return result


# ------------------------------------------------------------------
# Imposter guess (optional bonus)
# ------------------------------------------------------------------

def submit_imposter_guess(room: Room, player_id: str, guess: str) -> bool:
    """Let the imposter guess the normal content during REVEALING_RESULTS.

    Returns True if the guess is correct (bonus awarded), False otherwise.
    Raises GameError if called by the wrong player, wrong phase, or twice.

    Matching is lenient: case-insensitive, punctuation-stripped substring
    match either way (e.g. "LeBron" matches "LeBron James dunking").
    """
    if room.state != GameState.REVEALING_RESULTS or not room.current_round:
        raise GameError("Guesses are only accepted right after the vote")

    round_ = room.current_round
    if player_id != round_.imposter_id:
        raise GameError("Only the imposter can guess the normal content")

    player = room.players[player_id]
    if player.imposter_guess is not None:
        raise GameError("You've already submitted your guess")

    cleaned = (guess or "").strip()[:_MAX_GUESS_LEN]
    if not cleaned:
        raise GameError("Guess cannot be empty")

    player.imposter_guess = cleaned
    correct = _guess_matches_normal(cleaned, round_.content_pair.normal)

    if round_.result is not None:
        round_.result.imposter_guess = cleaned
        round_.result.imposter_guess_correct = correct
        if correct:
            player.score += POINTS_IMPOSTER_GUESS
            round_.result.score_delta[player.name] = (
                round_.result.score_delta.get(player.name, 0) + POINTS_IMPOSTER_GUESS
            )

    return correct


# ------------------------------------------------------------------
# Between rounds
# ------------------------------------------------------------------

def show_scoreboard(room: Room) -> None:
    """REVEALING_RESULTS -> SCOREBOARD. Host-driven."""
    if room.state != GameState.REVEALING_RESULTS:
        raise GameError("Scoreboard is only shown after results")
    room.state = GameState.SCOREBOARD
    room.phase_ends_at = None


def is_game_over(room: Room) -> bool:
    """True if the configured number of rounds have been played."""
    return room.rounds_played >= room.settings.total_rounds


def next_round(
    room: Room,
    content_pair: ContentPair,
    imposter_id: Optional[str] = None,
) -> Round:
    """Start the next round.

    Accepts SCOREBOARD (the normal path) and REVEALING_RESULTS (back-compat).
    Raises if the configured total_rounds have already been played — reset
    the game in that case.
    """
    if room.state not in (GameState.SCOREBOARD, GameState.REVEALING_RESULTS):
        raise GameError("Can only start the next round from the results screen")
    if is_game_over(room):
        raise GameError("Game is over — reset to play again")
    return start_round(room, content_pair, imposter_id)


def reset_game(room: Room) -> None:
    """Send the room all the way back to WAITING_LOBBY, keeping the players.

    Wipes all per-round state AND cumulative scores. Used for "new game"
    and to recover from a mid-round situation the host wants to abandon.
    """
    room.state = GameState.WAITING_LOBBY
    room.phase_ends_at = None
    room.current_round = None
    room.rounds_played = 0
    for p in room.players.values():
        _reset_player_round_state(p)
        p.score = 0


# =============================================================================
# View serialization
#
# Lives in this module because "what's public vs private" is a game rule —
# e.g. the imposter's identity stays hidden until REVEALING_RESULTS.
# =============================================================================

def serialize_room(room: Room, for_player_id: Optional[str] = None) -> dict:
    """Build a dict snapshot safe to send over the wire.

    - Other players' private content and the imposter's identity are kept
      out of the public view and only surface when the rules allow.
    - `for_player_id`, if supplied, adds a `me` block with that player's
      own private content (during SHOWING_CONTENT only), their own word,
      and their own vote.
    """
    round_ = room.current_round
    voter_ids = {v.voter_id for v in round_.votes} if round_ else set()

    def public_player(p: Player) -> dict:
        view: dict = {
            "id": p.id,
            "name": p.name,
            "is_host": p.is_host,
            "connected": p.connected,
            "has_word": bool(p.word),
            "has_voted": p.id in voter_ids,
            "score": p.score,
        }
        # Words become public from the reveal phase onward.
        if room.state in (GameState.REVEALING_WORDS,
                          GameState.DISCUSSION,
                          GameState.VOTING,
                          GameState.REVEALING_RESULTS,
                          GameState.SCOREBOARD):
            view["word"] = p.word
        # Imposter identity is only disclosed after the round is over.
        if room.state in (GameState.REVEALING_RESULTS, GameState.SCOREBOARD):
            view["was_imposter"] = p.is_imposter
        return view

    snap: dict = {
        "code": room.code,
        # Exposed as "phase" so existing clients don't need to learn a new key.
        "phase": room.state.value,
        "phase_ends_at": room.phase_ends_at,
        "round": room.rounds_played,
        "host_id": room.host_id,
        "players": [public_player(p) for p in room.players.values()],
        "min_players": MIN_PLAYERS,
        "max_players": MAX_PLAYERS,
        "settings": _serialize_settings(room.settings),
        "total_rounds": room.settings.total_rounds,
        "game_over": is_game_over(room) and room.state == GameState.SCOREBOARD,
        "last_result": _serialize_result(round_.result)
                       if (round_ and round_.result
                           and room.state in (GameState.REVEALING_RESULTS,
                                              GameState.SCOREBOARD))
                       else None,
    }

    if for_player_id and for_player_id in room.players:
        me = room.players[for_player_id]
        my_vote = next(
            (v.target_id for v in (round_.votes if round_ else [])
             if v.voter_id == for_player_id),
            None,
        )
        snap["me"] = {
            "id": me.id,
            "name": me.name,
            "is_host": me.is_host,
            # Imposter identity only surfaces once the round is resolved.
            "is_imposter": me.is_imposter if room.state in (
                GameState.REVEALING_RESULTS, GameState.SCOREBOARD
            ) else None,
            # Private content is exposed ONLY during SHOWING_CONTENT and ONLY
            # in the requesting player's own `me` block.
            "content": me.private_content if room.state == GameState.SHOWING_CONTENT else None,
            "content_kind": me.content_kind,
            "word": me.word,
            "vote": my_vote,
            "score": me.score,
            "imposter_guess": me.imposter_guess,
        }

    return snap


def _serialize_settings(s: RoomSettings) -> dict:
    return {
        "total_rounds": s.total_rounds,
        "category": s.category,
        "max_players": s.max_players,
        "number_of_imposters": s.number_of_imposters,
        "showing_content_seconds": s.showing_content_seconds,
        "word_submission_seconds": s.word_submission_seconds,
        "discussion_seconds": s.discussion_seconds,
        "voting_seconds": s.voting_seconds,
        "available_categories": ALL_CATEGORIES,
    }


def _serialize_result(r: RoundResult) -> dict:
    return {
        "round": r.round_number,
        "imposter_id": r.imposter_id,
        "imposter_name": r.imposter_name,
        "vote_counts": r.vote_counts,
        "words": r.words,
        "normal_content": r.normal_content,
        "imposter_content": r.imposter_content,
        "content_kind": r.content_kind,
        "content_category": r.content_category,
        "group_won": r.group_won,
        "imposter_won": r.imposter_won,
        "tied": r.tied,
        "accused_names": r.accused_names,
        "score_delta": r.score_delta,
        "imposter_guess": r.imposter_guess,
        "imposter_guess_correct": r.imposter_guess_correct,
    }

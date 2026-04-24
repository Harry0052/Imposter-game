"""Tests for game_logic. No framework deps — run directly:

    python tests/test_game_logic.py

The whole point of keeping `game_logic.py` framework-free is that these
tests don't need FastAPI, TestClient, or any web machinery. They pass in
Room objects and read the results out.
"""

from __future__ import annotations

import os
import sys

# Allow running from the repo root OR from inside tests/.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import game_logic as g
from game_logic import ContentPair, GameError, GameState


# ---------- Fixtures ----------

def make_room(n: int = 3) -> g.Room:
    """Room with `n` players. The first is Alice (host)."""
    room = g.create_room("Alice")
    for name in ["Bob", "Cara", "Dan", "Eve", "Fin", "Gus", "Hana", "Iri", "Jo"][:n - 1]:
        g.add_player(room, name)
    return room


def ids(room: g.Room):
    return list(room.players.keys())


def walk_to_voting(room: g.Room, imposter_id: str, word: str = "x") -> None:
    g.start_round(room, ContentPair("normal", "impostr"), imposter_id=imposter_id)
    g.end_showing_content(room)
    for pid in ids(room):
        g.submit_word(room, pid, word)
    g.reveal_words(room)
    g.start_voting(room)


# ---------- create_room / add_player / remove_player ----------

def test_create_room_defaults():
    room = g.create_room("Alice")
    assert len(room.code) == 5
    assert room.state == GameState.WAITING_LOBBY
    assert room.rounds_played == 0
    assert len(room.players) == 1
    host = next(iter(room.players.values()))
    assert host.is_host and host.name == "Alice"
    assert room.host_id == host.id


def test_create_room_avoids_existing_codes():
    room1 = g.create_room("A")
    room2 = g.create_room("B", existing_codes={room1.code})
    assert room1.code != room2.code


def test_add_player_enforces_max():
    room = g.create_room("Host")
    for i in range(g.MAX_PLAYERS - 1):
        g.add_player(room, f"P{i}")
    try:
        g.add_player(room, "Extra")
        assert False, "should raise when full"
    except GameError as e:
        assert "full" in str(e).lower()


def test_add_player_rejects_empty_name():
    room = g.create_room("Host")
    try:
        g.add_player(room, "   ")
        assert False
    except GameError as e:
        assert "empty" in str(e).lower()


def test_add_player_rejects_mid_game():
    room = make_room(3)
    g.start_round(room, ContentPair("a", "b"), imposter_id=ids(room)[0])
    try:
        g.add_player(room, "Latecomer")
        assert False
    except GameError as e:
        assert "progress" in str(e).lower()


def test_remove_player_in_lobby_promotes_new_host():
    room = make_room(3)
    host_id = room.host_id
    g.remove_player(room, host_id)
    assert host_id not in room.players
    assert len(room.players) == 2
    assert room.host_id in room.players
    assert room.players[room.host_id].is_host


def test_remove_player_mid_game_marks_disconnected():
    room = make_room(3)
    g.start_round(room, ContentPair("a", "b"), imposter_id=ids(room)[0])
    drop = ids(room)[1]
    g.remove_player(room, drop)
    # Still in the dict, just flagged offline.
    assert drop in room.players
    assert room.players[drop].connected is False


# ---------- start_round / assign_content ----------

def test_start_round_requires_min_players():
    room = g.create_room("Alice")
    g.add_player(room, "Bob")
    try:
        g.start_round(room, ContentPair("a", "b"))
        assert False
    except GameError as e:
        assert str(g.MIN_PLAYERS) in str(e) or "players" in str(e).lower()


def test_start_round_assigns_content_privately():
    room = make_room(3)
    pair = ContentPair(normal="apple", imposter="orange")
    impostor_id = ids(room)[0]
    g.start_round(room, pair, imposter_id=impostor_id)

    assert room.state == GameState.SHOWING_CONTENT
    assert room.current_round.imposter_id == impostor_id
    assert room.rounds_played == 1

    imp = room.players[impostor_id]
    assert imp.is_imposter and imp.private_content == "orange"
    for pid in ids(room)[1:]:
        p = room.players[pid]
        assert not p.is_imposter and p.private_content == "apple"


def test_start_round_random_imposter_is_always_a_member():
    room = make_room(5)
    for _ in range(10):
        g.reset_game(room)
        g.start_round(room, ContentPair("a", "b"))
        assert room.current_round.imposter_id in room.players


def test_start_round_rejects_foreign_imposter_id():
    room = make_room(3)
    try:
        g.start_round(room, ContentPair("a", "b"), imposter_id="nope")
        assert False
    except GameError:
        pass


def test_end_showing_content_hides_private_content():
    room = make_room(3)
    g.start_round(room, ContentPair("a", "b"), imposter_id=ids(room)[0])
    g.end_showing_content(room)
    assert room.state == GameState.SUBMITTING_WORDS
    assert all(p.private_content is None for p in room.players.values())


# ---------- submit_word ----------

def test_submit_word_rejects_multiple_words():
    room = make_room(3)
    g.start_round(room, ContentPair("a", "b"), imposter_id=ids(room)[0])
    g.end_showing_content(room)
    try:
        g.submit_word(room, ids(room)[0], "two words")
        assert False
    except GameError as e:
        assert "one word" in str(e).lower()


def test_submit_word_rejects_empty():
    room = make_room(3)
    g.start_round(room, ContentPair("a", "b"), imposter_id=ids(room)[0])
    g.end_showing_content(room)
    try:
        g.submit_word(room, ids(room)[0], "   ")
        assert False
    except GameError as e:
        assert "empty" in str(e).lower()


def test_submit_word_cannot_be_overwritten():
    room = make_room(3)
    pid = ids(room)[0]
    g.start_round(room, ContentPair("a", "b"), imposter_id=pid)
    g.end_showing_content(room)
    g.submit_word(room, pid, "first")
    assert room.players[pid].word == "first"
    try:
        g.submit_word(room, pid, "second")
        assert False
    except GameError as e:
        assert "locked" in str(e).lower()
    # Original word preserved.
    assert room.players[pid].word == "first"


def test_submit_word_rejects_wrong_phase():
    room = make_room(3)
    try:
        g.submit_word(room, ids(room)[0], "hi")
        assert False
    except GameError:
        pass


def test_all_words_submitted_flag():
    room = make_room(3)
    g.start_round(room, ContentPair("a", "b"), imposter_id=ids(room)[0])
    g.end_showing_content(room)
    assert not g.all_words_submitted(room)
    for pid in ids(room):
        g.submit_word(room, pid, "ok")
    assert g.all_words_submitted(room)


# ---------- reveal_words / voting ----------

def test_reveal_words_returns_all_words():
    room = make_room(3)
    g.start_round(room, ContentPair("a", "b"), imposter_id=ids(room)[0])
    g.end_showing_content(room)
    for pid in ids(room):
        g.submit_word(room, pid, "fruit")
    words = g.reveal_words(room)
    assert set(words.values()) == {"fruit"}
    assert room.state == GameState.REVEALING_WORDS


def test_cannot_vote_outside_voting_phase():
    room = make_room(3)
    # In the lobby
    try:
        g.submit_vote(room, ids(room)[0], ids(room)[1])
        assert False
    except GameError:
        pass

    # In showing_content
    g.start_round(room, ContentPair("a", "b"), imposter_id=ids(room)[0])
    try:
        g.submit_vote(room, ids(room)[0], ids(room)[1])
        assert False
    except GameError:
        pass


def test_cannot_vote_for_self():
    room = make_room(3)
    walk_to_voting(room, imposter_id=ids(room)[0])
    try:
        g.submit_vote(room, ids(room)[0], ids(room)[0])
        assert False
    except GameError as e:
        assert "yourself" in str(e).lower()


def test_cannot_vote_twice():
    room = make_room(3)
    walk_to_voting(room, imposter_id=ids(room)[0])
    g.submit_vote(room, ids(room)[1], ids(room)[0])
    try:
        g.submit_vote(room, ids(room)[1], ids(room)[2])
        assert False
    except GameError as e:
        assert "already" in str(e).lower()
    # Original vote is still the one that counts.
    votes = [v for v in room.current_round.votes if v.voter_id == ids(room)[1]]
    assert len(votes) == 1 and votes[0].target_id == ids(room)[0]


# ---------- calculate_results ----------

def test_group_wins_on_clean_majority():
    room = make_room(4)
    pids = ids(room)
    imposter = pids[0]
    walk_to_voting(room, imposter_id=imposter)
    # Everyone blames the imposter; imposter votes someone else.
    for pid in pids:
        target = imposter if pid != imposter else pids[1]
        g.submit_vote(room, pid, target)
    r = g.calculate_results(room)
    assert r.group_won and not r.imposter_won and not r.tied
    assert r.accused_names == [room.players[imposter].name]
    assert room.state == GameState.REVEALING_RESULTS


def test_imposter_wins_when_innocent_is_voted_out():
    room = make_room(4)
    pids = ids(room)
    imposter = pids[0]
    innocent = pids[1]
    walk_to_voting(room, imposter_id=imposter)
    # Everyone except the innocent votes innocent; innocent votes imposter.
    for pid in pids:
        target = imposter if pid == innocent else innocent
        g.submit_vote(room, pid, target)
    r = g.calculate_results(room)
    assert not r.group_won and r.imposter_won and not r.tied


def test_tie_counts_as_imposter_win_and_lists_all_tied():
    room = make_room(4)
    pids = ids(room)
    imposter = pids[0]
    walk_to_voting(room, imposter_id=imposter)
    # 2 votes for pids[0], 2 votes for pids[1] — a 2-way tie.
    g.submit_vote(room, pids[0], pids[1])
    g.submit_vote(room, pids[1], pids[0])
    g.submit_vote(room, pids[2], pids[1])
    g.submit_vote(room, pids[3], pids[0])
    r = g.calculate_results(room)
    assert r.tied and not r.group_won and r.imposter_won
    assert sorted(r.accused_names) == sorted([room.players[pids[0]].name,
                                              room.players[pids[1]].name])


def test_calculate_results_requires_voting_phase():
    room = make_room(3)
    try:
        g.calculate_results(room)
        assert False
    except GameError:
        pass


# ---------- next_round / reset_game ----------

def test_next_round_only_from_results():
    room = make_room(3)
    try:
        g.next_round(room, ContentPair("a", "b"))
        assert False
    except GameError:
        pass


def test_next_round_starts_round_two():
    room = make_room(3)
    pids = ids(room)
    walk_to_voting(room, imposter_id=pids[0])
    for pid in pids[1:]:
        g.submit_vote(room, pid, pids[0])
    g.submit_vote(room, pids[0], pids[1])
    g.calculate_results(room)

    g.next_round(room, ContentPair("c", "d"), imposter_id=pids[1])
    assert room.rounds_played == 2
    assert room.state == GameState.SHOWING_CONTENT
    assert room.current_round.imposter_id == pids[1]


def test_reset_game_clears_round_state():
    room = make_room(3)
    pids = ids(room)
    g.start_round(room, ContentPair("a", "b"), imposter_id=pids[0])
    g.end_showing_content(room)
    g.submit_word(room, pids[0], "x")

    g.reset_game(room)
    assert room.state == GameState.WAITING_LOBBY
    assert room.current_round is None
    assert room.rounds_played == 0
    assert all(p.word is None and not p.is_imposter for p in room.players.values())


# ---------- serialize_room: privacy rules ----------

def test_private_content_only_in_own_me_block():
    room = make_room(3)
    pids = ids(room)
    imposter = pids[0]
    g.start_round(room, ContentPair("apple", "orange"), imposter_id=imposter)

    snap = g.serialize_room(room, for_player_id=imposter)
    assert snap["me"]["content"] == "orange"
    # No public player entry leaks the content.
    for p in snap["players"]:
        assert "content" not in p
        assert "private_content" not in p


def test_imposter_identity_hidden_until_results():
    room = make_room(3)
    pids = ids(room)
    imposter = pids[0]
    walk_to_voting(room, imposter_id=imposter)

    snap = g.serialize_room(room, for_player_id=pids[1])
    # During voting, nobody's `was_imposter` is leaked.
    for p in snap["players"]:
        assert "was_imposter" not in p
    # The imposter's own `me.is_imposter` is hidden until results too.
    snap_imp = g.serialize_room(room, for_player_id=imposter)
    assert snap_imp["me"]["is_imposter"] is None


# =============================================================================
# Settings / categories / discussion / scoring / scoreboard
# =============================================================================

def test_default_settings_exist():
    room = g.create_room("Alice")
    s = room.settings
    assert s.total_rounds == 5
    assert s.category is None
    assert s.max_players == g.MAX_PLAYERS
    assert s.number_of_imposters == 1
    # Timers default to module-level constants.
    assert s.voting_seconds == g.VOTING_SECONDS


def test_update_settings_partial_patch():
    room = make_room(3)
    g.update_settings(room, {"total_rounds": 3, "category": "music"})
    assert room.settings.total_rounds == 3
    assert room.settings.category == "music"
    # Other fields untouched.
    assert room.settings.max_players == g.MAX_PLAYERS


def test_update_settings_rejects_bad_values():
    room = make_room(3)
    for patch in (
        {"total_rounds": 0},
        {"total_rounds": 50},
        {"category": "not_a_category"},
        {"max_players": 1},
        {"max_players": 99},
        {"number_of_imposters": 3},
        {"discussion_seconds": 2},
    ):
        try:
            g.update_settings(room, patch)
            assert False, f"should have rejected {patch}"
        except GameError:
            pass


def test_update_settings_any_clears_category_filter():
    room = make_room(3)
    g.update_settings(room, {"category": "music"})
    assert room.settings.category == "music"
    g.update_settings(room, {"category": "any"})
    assert room.settings.category is None


def test_update_settings_locked_during_game():
    room = make_room(3)
    g.start_round(room, ContentPair("a", "b", category="music"), imposter_id=ids(room)[0])
    try:
        g.update_settings(room, {"total_rounds": 3})
        assert False
    except GameError as e:
        assert "lobby" in str(e).lower()


def test_max_players_cap_enforced():
    room = g.create_room("Host")
    g.update_settings(room, {"max_players": 4})
    g.add_player(room, "Bob")
    g.add_player(room, "Cara")
    g.add_player(room, "Dan")
    try:
        g.add_player(room, "Eve")
        assert False
    except GameError as e:
        assert "full" in str(e).lower()


def test_content_pair_has_category_field():
    cp = ContentPair(normal="x", imposter="y", category="sports")
    assert cp.category == "sports"


def test_pick_random_pair_respects_category():
    from content import pick_random_pair, CONTENT_BANK
    # Verify we have some content in each configured category.
    categories = {cp.category for cp in CONTENT_BANK}
    for cat in ("influencers", "sports", "music", "memes"):
        assert cat in categories, f"no {cat} content"
        pair = pick_random_pair(cat)
        assert pair.category == cat


# --- Discussion phase ---

def test_start_discussion_transitions_correctly():
    room = make_room(3)
    pids = ids(room)
    g.start_round(room, ContentPair("a", "b"), imposter_id=pids[0])
    g.end_showing_content(room)
    for pid in pids:
        g.submit_word(room, pid, "x")
    g.reveal_words(room)
    assert room.state == g.GameState.REVEALING_WORDS
    g.start_discussion(room)
    assert room.state == g.GameState.DISCUSSION
    assert room.phase_ends_at is not None


def test_start_voting_from_discussion():
    room = make_room(3)
    pids = ids(room)
    g.start_round(room, ContentPair("a", "b"), imposter_id=pids[0])
    g.end_showing_content(room)
    for pid in pids:
        g.submit_word(room, pid, "x")
    g.reveal_words(room)
    g.start_discussion(room)
    g.start_voting(room)
    assert room.state == g.GameState.VOTING


# --- Scoring ---

def test_group_win_awards_points_to_non_imposters():
    room = make_room(4)
    pids = ids(room)
    imposter = pids[0]
    walk_to_voting(room, imposter_id=imposter)
    for pid in pids:
        target = imposter if pid != imposter else pids[1]
        g.submit_vote(room, pid, target)
    result = g.calculate_results(room)

    # Non-imposters gain POINTS_GROUP_WIN; imposter gains 0.
    assert result.group_won
    for pid in pids:
        player = room.players[pid]
        if pid == imposter:
            assert player.score == 0
        else:
            assert player.score == g.POINTS_GROUP_WIN
    # Score delta on the result matches what we awarded.
    assert sum(result.score_delta.values()) == (len(pids) - 1) * g.POINTS_GROUP_WIN


def test_imposter_win_awards_points_to_imposter_only():
    room = make_room(4)
    pids = ids(room)
    imposter = pids[0]
    innocent = pids[1]
    walk_to_voting(room, imposter_id=imposter)
    for pid in pids:
        target = imposter if pid == innocent else innocent
        g.submit_vote(room, pid, target)
    result = g.calculate_results(room)

    assert result.imposter_won
    assert room.players[imposter].score == g.POINTS_IMPOSTER_WIN
    for pid in pids:
        if pid != imposter:
            assert room.players[pid].score == 0


def test_imposter_guess_bonus_when_correct():
    room = make_room(3)
    pids = ids(room)
    imposter = pids[0]
    # Set up a round where the normal content is "LeBron James dunking".
    g.start_round(room, ContentPair("LeBron James dunking", "Curry shooting threes"),
                  imposter_id=imposter)
    g.end_showing_content(room)
    for pid in pids: g.submit_word(room, pid, "x")
    g.reveal_words(room); g.start_discussion(room); g.start_voting(room)
    for pid in pids[1:]:
        g.submit_vote(room, pid, imposter)
    g.submit_vote(room, imposter, pids[1])
    result = g.calculate_results(room)
    assert result.group_won

    # Imposter got 0 points from the loss.
    imposter_score_before = room.players[imposter].score

    # Now guess the normal content — partial match should still be "correct".
    correct = g.submit_imposter_guess(room, imposter, "LeBron dunking")
    assert correct is True
    assert room.players[imposter].score == imposter_score_before + g.POINTS_IMPOSTER_GUESS
    assert result.imposter_guess_correct is True
    assert result.imposter_guess == "LeBron dunking"
    assert result.score_delta.get(room.players[imposter].name) == g.POINTS_IMPOSTER_GUESS


def test_imposter_guess_no_bonus_when_wrong():
    room = make_room(3)
    pids = ids(room)
    imposter = pids[0]
    g.start_round(room, ContentPair("LeBron James dunking", "Curry threes"),
                  imposter_id=imposter)
    g.end_showing_content(room)
    for pid in pids: g.submit_word(room, pid, "x")
    g.reveal_words(room); g.start_discussion(room); g.start_voting(room)
    for pid in pids[1:]:
        g.submit_vote(room, pid, imposter)
    g.submit_vote(room, imposter, pids[1])
    g.calculate_results(room)

    imposter_score_before = room.players[imposter].score
    correct = g.submit_imposter_guess(room, imposter, "banana bread")
    assert correct is False
    assert room.players[imposter].score == imposter_score_before


def test_imposter_guess_rejects_non_imposter():
    room = make_room(3)
    pids = ids(room)
    imposter = pids[0]
    walk_to_voting(room, imposter_id=imposter)
    for pid in pids[1:]:
        g.submit_vote(room, pid, imposter)
    g.submit_vote(room, imposter, pids[1])
    g.calculate_results(room)
    try:
        g.submit_imposter_guess(room, pids[1], "anything")
        assert False
    except GameError as e:
        assert "imposter" in str(e).lower()


def test_imposter_guess_rejects_double_submit():
    room = make_room(3)
    pids = ids(room)
    imposter = pids[0]
    walk_to_voting(room, imposter_id=imposter)
    for pid in pids[1:]:
        g.submit_vote(room, pid, imposter)
    g.submit_vote(room, imposter, pids[1])
    g.calculate_results(room)
    g.submit_imposter_guess(room, imposter, "foo")
    try:
        g.submit_imposter_guess(room, imposter, "bar")
        assert False
    except GameError as e:
        assert "already" in str(e).lower()


# --- Scoreboard ---

def test_show_scoreboard_transition():
    room = make_room(3)
    pids = ids(room)
    walk_to_voting(room, imposter_id=pids[0])
    for pid in pids[1:]:
        g.submit_vote(room, pid, pids[0])
    g.submit_vote(room, pids[0], pids[1])
    g.calculate_results(room)
    assert room.state == g.GameState.REVEALING_RESULTS
    g.show_scoreboard(room)
    assert room.state == g.GameState.SCOREBOARD


def test_game_over_after_total_rounds():
    room = make_room(3)
    g.update_settings(room, {"total_rounds": 2})
    pids = ids(room)

    def play_one_round():
        g.start_round(room, ContentPair("a", "b"), imposter_id=pids[0])
        g.end_showing_content(room)
        for pid in pids: g.submit_word(room, pid, "x")
        g.reveal_words(room); g.start_discussion(room); g.start_voting(room)
        for pid in pids[1:]: g.submit_vote(room, pid, pids[0])
        g.submit_vote(room, pids[0], pids[1])
        g.calculate_results(room)
        g.show_scoreboard(room)

    play_one_round()
    assert not g.is_game_over(room)
    g.next_round(room, ContentPair("c", "d"), imposter_id=pids[0])  # round 2
    # finish round 2
    g.end_showing_content(room)
    for pid in pids: g.submit_word(room, pid, "x")
    g.reveal_words(room); g.start_discussion(room); g.start_voting(room)
    for pid in pids[1:]: g.submit_vote(room, pid, pids[0])
    g.submit_vote(room, pids[0], pids[1])
    g.calculate_results(room); g.show_scoreboard(room)

    assert g.is_game_over(room)
    # next_round now raises — host must reset to play again.
    try:
        g.next_round(room, ContentPair("e", "f"), imposter_id=pids[0])
        assert False
    except GameError as e:
        assert "over" in str(e).lower() or "reset" in str(e).lower()


def test_reset_game_clears_scores_too():
    room = make_room(3)
    pids = ids(room)
    walk_to_voting(room, imposter_id=pids[0])
    for pid in pids[1:]: g.submit_vote(room, pid, pids[0])
    g.submit_vote(room, pids[0], pids[1])
    g.calculate_results(room)
    # After a group win at least one player has > 0 score.
    assert sum(p.score for p in room.players.values()) > 0
    g.reset_game(room)
    assert all(p.score == 0 for p in room.players.values())


# --- Serialization carries settings + scores ---

def test_serialize_room_includes_settings_and_scores():
    room = make_room(3)
    g.update_settings(room, {"total_rounds": 7, "category": "memes"})
    snap = g.serialize_room(room)
    assert snap["settings"]["total_rounds"] == 7
    assert snap["settings"]["category"] == "memes"
    assert "available_categories" in snap["settings"]
    # Player entries always carry the running score.
    assert all("score" in p for p in snap["players"])


# =============================================================================
# Runner
# =============================================================================

def run_tests() -> bool:
    tests = [(n, fn) for n, fn in sorted(globals().items()) if n.startswith("test_")]
    width = max(len(n) for n, _ in tests) + 2
    passed, failed = 0, 0
    for name, fn in tests:
        try:
            fn()
            print(f"  OK   {name:<{width}}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL {name:<{width}} {e}")
            failed += 1
        except Exception as e:
            print(f"  ERR  {name:<{width}} {type(e).__name__}: {e}")
            failed += 1
    total = passed + failed
    print(f"\n{passed}/{total} tests passed.")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run_tests() else 1)

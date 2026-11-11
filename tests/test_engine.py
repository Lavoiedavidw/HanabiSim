import pytest

from hanabi.bots import RuleBot, describe_view, parse_move, play_bots_only, possible_identities
from hanabi.engine import COLORS, Card, Game, IllegalMove


def stacked(game, player, cards):
    """Replace a player's hand with known cards."""
    game.hands[player] = [Card(100 + i, c, r) for i, (c, r) in enumerate(cards)]


def test_deal():
    assert [len(h) for h in Game(["a", "b", "c"], seed=1).hands] == [5, 5, 5]
    g = Game(["a", "b", "c", "d"], seed=1)
    assert [len(h) for h in g.hands] == [4, 4, 4, 4]
    assert len(g.deck) == 50 - 16
    with pytest.raises(ValueError):
        Game(["solo"])


def test_play_success_and_strike():
    g = Game(["a", "b"], seed=1)
    stacked(g, 0, [("red", 1), ("red", 3)])
    stacked(g, 1, [("blue", 2)])
    assert g.apply({"type": "play", "index": 0})["success"]
    assert g.stacks["red"] == 1 and g.current == 1
    assert not g.apply({"type": "play", "index": 0})["success"]
    assert g.strikes == 1 and g.discard_pile[-1].color == "blue"
    assert g.hands[0][0].id < 100  # drew a replacement into slot 1


def test_clue_marks_cards_and_costs_a_token():
    g = Game(["a", "b"], seed=1)
    stacked(g, 1, [("red", 1), ("blue", 1), ("red", 2)])
    entry = g.apply({"type": "clue", "target": 1, "color": "red"})
    assert entry["touched"] == [0, 2] and g.clues == 7
    assert g.hands[1][0].known_color == "red"
    assert g.hands[1][1].not_colors == {"red"}


def test_illegal_moves():
    g = Game(["a", "b"], seed=1)
    stacked(g, 1, [("red", 1)])
    for bad in (
        {"type": "discard", "index": 0},  # 8 clue tokens
        {"type": "clue", "target": 0, "rank": 1},  # self
        {"type": "clue", "target": 1, "color": "blue"},  # touches nothing
        {"type": "clue", "target": 1, "color": "red", "rank": 1},
        {"type": "play", "index": 9},
    ):
        with pytest.raises(IllegalMove):
            g.apply(bad)
    g.clues = 0
    with pytest.raises(IllegalMove):
        g.apply({"type": "clue", "target": 1, "rank": 1})


def test_five_returns_a_clue_token():
    g = Game(["a", "b"], seed=1)
    g.stacks["green"] = 4
    g.clues = 3
    stacked(g, 0, [("green", 5)])
    g.apply({"type": "play", "index": 0})
    assert g.clues == 4


def test_three_strikes_ends_the_game():
    g = Game(["a", "b"], seed=1)
    for _ in range(3):
        stacked(g, g.current, [("red", 5)])
        g.apply({"type": "play", "index": 0})
    assert g.over and g.lost


def test_everyone_gets_one_turn_after_the_deck_runs_out():
    g = Game(["a", "b", "c"], seed=1)
    g.deck = g.deck[:1]
    g.clues = 0
    g.apply({"type": "discard", "index": 0})  # a draws the last card
    assert g.turns_left == 3 and not g.over
    turns = 0
    while not g.over:
        g.apply({"type": "discard", "index": 0})
        turns += 1
    assert turns == 3 and g.current == 0  # a had the final turn


def test_view_hides_own_cards():
    g = Game(["a", "b"], seed=1)
    v = g.view(0)
    assert all("color" not in c for c in v["hands"][0])
    assert all("color" in c for c in v["hands"][1])
    text = describe_view(v)
    for card in g.hands[0]:
        assert f"slot 1: {card.color}" not in text.split("b's hand")[0]


def test_possible_identities_uses_clues_and_visible_cards():
    g = Game(["a", "b"], seed=1)
    stacked(g, 0, [("white", 5)])
    stacked(g, 1, [("red", 5), ("blue", 5), ("green", 5), ("yellow", 5)])
    g.hands[0][0].known_rank = 5
    v = g.view(0)
    assert possible_identities(v, 0, v["hands"][0][0]) == [("white", 5)]


def test_parse_move():
    v = Game(["Ann", "Bob"], seed=1).view(0)
    assert parse_move(v, {"action": "play", "slot": 2}) == {"type": "play", "index": 1}
    assert parse_move(v, {"action": "clue", "target": "bob", "clue": "Red"}) == {"type": "clue", "target": 1, "color": "red"}
    assert parse_move(v, {"action": "clue", "target": "Bob", "clue": "3"}) == {"type": "clue", "target": 1, "rank": 3}
    with pytest.raises(ValueError):
        parse_move(v, {"action": "clue", "target": "Ann", "clue": "3"})


@pytest.mark.parametrize("n", [2, 3, 4, 5])
def test_rule_bots_finish_legal_games(n):
    scores = []
    for seed in range(30):
        g = play_bots_only([RuleBot() for _ in range(n)], seed=seed)
        assert g.over and 0 <= g.score <= 25
        assert all(0 <= g.stacks[c] <= 5 for c in COLORS)
        scores.append(g.score)
    assert sum(scores) / len(scores) > 8

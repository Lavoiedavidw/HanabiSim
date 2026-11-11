"""Hanabi rules engine. No dependencies, no I/O.

Hand order: index 0 is the newest card, the last index is the oldest.
Actions are plain dicts:
    {"type": "play", "index": 0}
    {"type": "discard", "index": 3}
    {"type": "clue", "target": 1, "color": "red"}
    {"type": "clue", "target": 1, "rank": 3}
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

COLORS = ["red", "yellow", "green", "blue", "white"]
RANKS = [1, 2, 3, 4, 5]
RANK_COUNTS = {1: 3, 2: 2, 3: 2, 4: 2, 5: 1}
MAX_CLUES = 8
MAX_STRIKES = 3


class IllegalMove(Exception):
    pass


@dataclass
class Card:
    id: int
    color: str
    rank: int
    # What the holder has been told about this card.
    known_color: str | None = None
    known_rank: int | None = None
    not_colors: set = field(default_factory=set)
    not_ranks: set = field(default_factory=set)

    @property
    def clued(self) -> bool:
        return self.known_color is not None or self.known_rank is not None

    def knowledge(self) -> dict:
        return {
            "id": self.id,
            "known_color": self.known_color,
            "known_rank": self.known_rank,
            "not_colors": sorted(self.not_colors, key=COLORS.index),
            "not_ranks": sorted(self.not_ranks),
        }

    def face(self) -> dict:
        return {**self.knowledge(), "color": self.color, "rank": self.rank}


def hand_size(n_players: int) -> int:
    return 5 if n_players <= 3 else 4


class Game:
    def __init__(self, names: list[str], seed: int | None = None):
        if not 2 <= len(names) <= 5:
            raise ValueError("Hanabi needs 2 to 5 players")
        self.names = list(names)
        self.n = len(names)
        self.seed = seed
        rng = random.Random(seed)
        self.deck = [
            (color, rank)
            for color in COLORS
            for rank in RANKS
            for _ in range(RANK_COUNTS[rank])
        ]
        rng.shuffle(self.deck)
        self._next_id = 0
        self.hands: list[list[Card]] = [[] for _ in names]
        for _ in range(hand_size(self.n)):
            for p in range(self.n):
                self._draw(p)
        self.stacks = {c: 0 for c in COLORS}
        self.discard_pile: list[Card] = []
        self.clues = MAX_CLUES
        self.strikes = 0
        self.current = 0
        self.turn = 0
        self.turns_left: int | None = None  # set once the deck runs out
        self.over = False
        self.log: list[dict] = []

    # ---- helpers -------------------------------------------------------

    def _draw(self, player: int) -> None:
        if self.deck:
            color, rank = self.deck.pop()
            self.hands[player].insert(0, Card(self._next_id, color, rank))
            self._next_id += 1

    @property
    def score(self) -> int:
        return sum(self.stacks.values())

    @property
    def lost(self) -> bool:
        return self.strikes >= MAX_STRIKES

    def is_playable(self, color: str, rank: int) -> bool:
        return self.stacks[color] == rank - 1

    def clue_touches(self, target: int, color=None, rank=None) -> list[int]:
        return [
            i
            for i, c in enumerate(self.hands[target])
            if (color is not None and c.color == color)
            or (rank is not None and c.rank == rank)
        ]

    def check(self, action: dict) -> None:
        """Raise IllegalMove if the current player may not take this action."""
        if self.over:
            raise IllegalMove("The game is over.")
        kind = action.get("type")
        hand = self.hands[self.current]
        if kind in ("play", "discard"):
            index = action.get("index")
            if not isinstance(index, int) or not 0 <= index < len(hand):
                raise IllegalMove(f"There is no card in slot {index!r}.")
            if kind == "discard" and self.clues >= MAX_CLUES:
                raise IllegalMove("You cannot discard while all 8 clue tokens are available.")
        elif kind == "clue":
            target = action.get("target")
            color, rank = action.get("color"), action.get("rank")
            if self.clues <= 0:
                raise IllegalMove("There are no clue tokens left.")
            if not isinstance(target, int) or not 0 <= target < self.n:
                raise IllegalMove("Unknown player.")
            if target == self.current:
                raise IllegalMove("You cannot give a clue to yourself.")
            if (color is None) == (rank is None):
                raise IllegalMove("A clue names exactly one color or one rank.")
            if color is not None and color not in COLORS:
                raise IllegalMove(f"Unknown color {color!r}.")
            if rank is not None and rank not in RANKS:
                raise IllegalMove(f"Unknown rank {rank!r}.")
            if not self.clue_touches(target, color, rank):
                raise IllegalMove("A clue must touch at least one card.")
        else:
            raise IllegalMove(f"Unknown action type {kind!r}.")

    def legal_actions(self) -> list[dict]:
        actions = []
        if self.over:
            return actions
        for i in range(len(self.hands[self.current])):
            actions.append({"type": "play", "index": i})
            if self.clues < MAX_CLUES:
                actions.append({"type": "discard", "index": i})
        if self.clues > 0:
            for t in range(self.n):
                if t == self.current:
                    continue
                for color in COLORS:
                    if self.clue_touches(t, color=color):
                        actions.append({"type": "clue", "target": t, "color": color})
                for rank in RANKS:
                    if self.clue_touches(t, rank=rank):
                        actions.append({"type": "clue", "target": t, "rank": rank})
        return actions

    # ---- the one state-changing method ----------------------------------

    def apply(self, action: dict) -> dict:
        """Apply the current player's action and return its log entry."""
        self.check(action)
        player = self.current
        me = self.names[player]
        final_round = self.turns_left is not None
        kind = action["type"]
        entry = {"turn": self.turn, "player": player, "type": kind}

        if kind == "clue":
            target, color, rank = action["target"], action.get("color"), action.get("rank")
            touched = self.clue_touches(target, color, rank)
            for i, card in enumerate(self.hands[target]):
                if color is not None:
                    if i in touched:
                        card.known_color = color
                    else:
                        card.not_colors.add(color)
                else:
                    if i in touched:
                        card.known_rank = rank
                    else:
                        card.not_ranks.add(rank)
            self.clues -= 1
            slots = ", ".join(str(i + 1) for i in touched)
            what = color if color is not None else f"rank {rank}"
            entry.update(target=target, color=color, rank=rank, touched=touched)
            entry["text"] = (
                f"{me} tells {self.names[target]}: slot{'s' if len(touched) > 1 else ''} "
                f"{slots} {'are' if len(touched) > 1 else 'is'} {what}."
            )
        else:
            card = self.hands[player].pop(action["index"])
            entry.update(index=action["index"], color=card.color, rank=card.rank)
            name = f"{card.color} {card.rank}"
            if kind == "play" and self.is_playable(card.color, card.rank):
                self.stacks[card.color] = card.rank
                entry["success"] = True
                entry["text"] = f"{me} plays {name} from slot {action['index'] + 1}."
                if card.rank == 5 and self.clues < MAX_CLUES:
                    self.clues += 1
                    entry["text"] += " The stack is complete: +1 clue token."
            elif kind == "play":
                self.strikes += 1
                self.discard_pile.append(card)
                entry["success"] = False
                entry["text"] = (
                    f"{me} tries to play {name} from slot {action['index'] + 1}. "
                    f"It does not fit: strike {self.strikes} of {MAX_STRIKES}."
                )
            else:
                self.clues += 1
                self.discard_pile.append(card)
                entry["text"] = f"{me} discards {name} from slot {action['index'] + 1}."
            self._draw(player)

        self.log.append(entry)
        self.turn += 1

        if final_round:
            self.turns_left -= 1
        elif not self.deck:
            self.turns_left = self.n  # everyone, including this player, gets one more turn
        if self.lost or self.score == 25 or self.turns_left == 0:
            self.over = True
        else:
            self.current = (self.current + 1) % self.n
        return entry

    # ---- views ----------------------------------------------------------

    def view(self, viewer: int | None) -> dict:
        """State as seen by `viewer`: their own cards show only clue knowledge.

        viewer=None gives the full state.
        """
        hands = []
        for p, hand in enumerate(self.hands):
            hands.append([c.knowledge() if p == viewer else c.face() for c in hand])
        return {
            "names": self.names,
            "viewer": viewer,
            "current": self.current,
            "turn": self.turn,
            "clues": self.clues,
            "strikes": self.strikes,
            "deck": len(self.deck),
            "score": self.score,
            "stacks": dict(self.stacks),
            "discard": [{"color": c.color, "rank": c.rank} for c in self.discard_pile],
            "hands": hands,
            "turns_left": self.turns_left,
            "over": self.over,
            "lost": self.lost,
            "log": [dict(e) for e in self.log],
        }

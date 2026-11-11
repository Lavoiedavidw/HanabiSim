"""Bots. Each bot has choose(view) -> (action, reasoning).

`view` is Game.view(seat): the bot never sees its own cards, only what it
has been told about them.
"""

from __future__ import annotations

import json

from .engine import COLORS, MAX_CLUES, RANK_COUNTS, RANKS, Game, IllegalMove


class BotError(Exception):
    """The bot could not produce a move (network, credentials, bad output)."""


# ---- shared reasoning about hidden cards ----------------------------------


def possible_identities(view: dict, seat: int, card: dict) -> list[tuple[str, int]]:
    """Every (color, rank) a card in `seat`'s own hand could still be."""
    remaining = {(c, r): RANK_COUNTS[r] for c in COLORS for r in RANKS}
    for color, top in view["stacks"].items():
        for rank in range(1, top + 1):
            remaining[(color, rank)] -= 1
    for c in view["discard"]:
        remaining[(c["color"], c["rank"])] -= 1
    for p, hand in enumerate(view["hands"]):
        if p != seat:
            for c in hand:
                remaining[(c["color"], c["rank"])] -= 1
    result = []
    for (color, rank), count in remaining.items():
        if count <= 0:
            continue
        if card["known_color"] not in (None, color) or color in card["not_colors"]:
            continue
        if card["known_rank"] not in (None, rank) or rank in card["not_ranks"]:
            continue
        result.append((color, rank))
    return result


def is_dead(view: dict, color: str, rank: int) -> bool:
    """True if this card can never be played (already played, or blocked)."""
    if rank <= view["stacks"][color]:
        return True
    discarded = {}
    for c in view["discard"]:
        if c["color"] == color:
            discarded[c["rank"]] = discarded.get(c["rank"], 0) + 1
    return any(
        discarded.get(r, 0) >= RANK_COUNTS[r]
        for r in range(view["stacks"][color] + 1, rank)
    )


def is_critical(view: dict, color: str, rank: int) -> bool:
    """True if this is the last copy of a card that is still needed."""
    if is_dead(view, color, rank):
        return False
    gone = sum(1 for c in view["discard"] if c["color"] == color and c["rank"] == rank)
    return gone == RANK_COUNTS[rank] - 1


def chop_index(hand: list[dict]) -> int | None:
    """The card a player discards next: the oldest one with no clue on it."""
    for i in range(len(hand) - 1, -1, -1):
        if hand[i]["known_color"] is None and hand[i]["known_rank"] is None:
            return i
    return None


# ---- rule-based bot ---------------------------------------------------------


class RuleBot:
    """A cautious bot with fixed rules. Needs no API key.

    It only plays cards it can prove are playable, tells partners about
    playable cards, protects critical cards about to be discarded, and
    otherwise discards its oldest unclued card.
    """

    kind = "rule"

    def choose(self, view: dict) -> tuple[dict, str]:
        me = view["viewer"]
        n = len(view["names"])
        stacks = view["stacks"]
        my_hand = view["hands"][me]
        options = [possible_identities(view, me, c) for c in my_hand]

        for i, ids in enumerate(options):
            if ids and all(stacks[c] == r - 1 for c, r in ids):
                return {"type": "play", "index": i}, "Every card this could be is playable."

        others = [(me + k) % n for k in range(1, n)]
        if view["clues"] > 0:
            for p in others:
                clue = self._play_clue(view, p)
                if clue:
                    return clue, f"{view['names'][p]} has a playable card."
            for p in others:
                hand = view["hands"][p]
                chop = chop_index(hand)
                if chop is not None and is_critical(view, hand[chop]["color"], hand[chop]["rank"]):
                    card = hand[chop]
                    clue = {"type": "clue", "target": p}
                    if card["rank"] == 5:
                        clue["rank"] = 5
                    else:
                        clue["color"] = card["color"]
                    return clue, f"Protecting {view['names'][p]}'s last {card['color']} {card['rank']}."

        if view["clues"] < MAX_CLUES:
            for i, ids in enumerate(options):
                if ids and all(is_dead(view, c, r) for c, r in ids):
                    return {"type": "discard", "index": i}, "This card is no longer needed."
            chop = chop_index(my_hand)
            if chop is None:
                chop = len(my_hand) - 1
            return {"type": "discard", "index": chop}, "Nothing better to do: discarding my oldest unclued card."

        # All 8 clue tokens are available, so discarding is not allowed.
        for p in others:
            hand = view["hands"][p]
            for card in hand:
                if card["known_rank"] is None:
                    return {"type": "clue", "target": p, "rank": card["rank"]}, "Cannot discard; giving information."
                if card["known_color"] is None:
                    return {"type": "clue", "target": p, "color": card["color"]}, "Cannot discard; giving information."
        p = others[0]
        return {"type": "clue", "target": p, "rank": view["hands"][p][0]["rank"]}, "Cannot discard."

    def _play_clue(self, view: dict, p: int) -> dict | None:
        """A clue that tells player p something new about a playable card."""
        hand = view["hands"][p]
        best = None
        for card in hand:
            if view["stacks"][card["color"]] != card["rank"] - 1:
                continue
            ids = possible_identities_for_other(view, p, card)
            if ids and all(view["stacks"][c] == r - 1 for c, r in ids):
                continue  # they can already work it out
            for key in ("rank", "color"):
                if card[f"known_{key}"] is not None:
                    continue
                touched = [c for c in hand if c[key] == card[key]]
                bad = sum(1 for c in touched if view["stacks"][c["color"]] != c["rank"] - 1)
                if best is None or bad < best[0]:
                    best = (bad, {"type": "clue", "target": p, key: card[key]})
        return best[1] if best else None


def possible_identities_for_other(view: dict, p: int, card: dict) -> list[tuple[str, int]]:
    """What player p could deduce about their own card, using only clue marks."""
    return [
        (c, r)
        for c in COLORS
        for r in RANKS
        if card["known_color"] in (None, c)
        and c not in card["not_colors"]
        and card["known_rank"] in (None, r)
        and r not in card["not_ranks"]
    ]


# ---- Claude bot -------------------------------------------------------------

CLAUDE_MODELS = {
    "claude-opus-5-5": "Claude Opus 5.5",
    "claude-sonnet-5-5": "Claude Sonnet 5.5",
    "claude-haiku-5-5": "Claude Haiku 5.5",
    "claude-fable-5-2": "Claude Fable 5.2",
}
DEFAULT_MODEL = "claude-opus-5-5"
# Models that support the server-side refusal fallback.
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2"}

SYSTEM_PROMPT = """You are playing Hanabi, the cooperative card game, as one of the players. \
Everyone at the table is on the same team and wants the highest shared score.

Rules
- The deck has 50 cards in five colors (red, yellow, green, blue, white). Each color has three 1s, \
two 2s, two 3s, two 4s and one 5.
- Each color has one stack, built in order from 1 to 5. The score is the sum of the top cards (max 25).
- You see every hand except your own. You only know what clues have told you about your cards.
- On your turn you do exactly one thing:
  - play: put one of your cards on the table. If it is the next card of its color it joins the stack; \
otherwise it is discarded and the team takes a strike. Completing a stack with a 5 returns one clue token.
  - discard: throw one of your cards away to regain one clue token. Not allowed when all 8 tokens are available.
  - clue: spend one clue token to tell one other player every card in their hand that has one \
color, or one rank. The clue must touch at least one card.
- After playing or discarding you draw a replacement, which goes in slot 1. Slots are numbered from \
1 (newest) upward; the highest slot is the oldest card.
- Three strikes ends the game as a loss. When the deck runs out, every player gets one last turn.

Playing well
- Clues are scarce. Prefer clues that let a partner play a card right away, or that save a card the \
team cannot afford to lose (any 5, or the last remaining copy of a card still needed).
- Players normally discard their oldest card that has no clue on it. Look at what partners are about \
to discard before deciding a clue is not needed.
- Use everything visible (other hands, the discard pile, the stacks) to narrow down what your own \
cards can be, and think about why a partner chose the clue they gave you.
- Do not play a card you only hope is playable when a strike would be costly.

Other players may be people or other bots, and they do not share any private convention with you \
beyond common Hanabi practice.

Reply with your move as JSON:
- action: "play", "discard" or "clue"
- slot: for play and discard, the slot number in your own hand. Use 0 for a clue.
- target: for a clue, the name of the player you are cluing. Use "" otherwise.
- clue: for a clue, the color name or the rank digit as text, such as "red" or "3". Use "" otherwise.
- reasoning: one or two sentences on why. Shown to the players after the game."""

MOVE_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "action": {"type": "string", "enum": ["play", "discard", "clue"]},
        "slot": {"type": "integer"},
        "target": {"type": "string"},
        "clue": {"type": "string"},
    },
    "required": ["reasoning", "action", "slot", "target", "clue"],
    "additionalProperties": False,
}


def describe_knowledge(card: dict) -> str:
    color = card["known_color"] or "?"
    rank = card["known_rank"] or "?"
    text = f"color {color}, rank {rank}"
    nots = []
    if card["known_color"] is None:
        nots += [f"not {c}" for c in card["not_colors"]]
    if card["known_rank"] is None:
        nots += [f"not {r}" for r in card["not_ranks"]]
    if nots:
        text += f" ({', '.join(nots)})"
    return text


def describe_view(view: dict) -> str:
    """The game as text, from the viewer's seat."""
    me = view["viewer"]
    names = view["names"]
    lines = [
        f"You are {names[me]}. Turn order: {', '.join(names)}.",
        f"Clue tokens: {view['clues']} of {MAX_CLUES}. Strikes: {view['strikes']} of 3. "
        f"Cards left in deck: {view['deck']}. Score: {view['score']}.",
    ]
    if view["turns_left"] is not None:
        lines.append(f"The deck is empty. Turns left in the game, including this one: {view['turns_left']}.")
    lines.append("Stacks (top card): " + ", ".join(f"{c} {view['stacks'][c]}" for c in COLORS))
    discard = sorted(view["discard"], key=lambda c: (COLORS.index(c["color"]), c["rank"]))
    lines.append("Discard pile: " + (", ".join(f"{c['color']} {c['rank']}" for c in discard) or "empty"))
    lines.append("")
    lines.append("Your hand (hidden from you; this is what you have been told):")
    for i, card in enumerate(view["hands"][me]):
        lines.append(f"  slot {i + 1}: {describe_knowledge(card)}")
    for p, hand in enumerate(view["hands"]):
        if p == me:
            continue
        lines.append(f"{names[p]}'s hand (what {names[p]} has been told is in brackets):")
        for i, card in enumerate(hand):
            lines.append(f"  slot {i + 1}: {card['color']} {card['rank']} [{describe_knowledge(card)}]")
    lines.append("")
    lines.append("Everything that has happened so far (slot numbers are as they were at that moment):")
    if not view["log"]:
        lines.append("  Nothing yet. You have the first turn.")
    for entry in view["log"]:
        lines.append(f"  {entry['turn'] + 1}. {entry['text']}")
    lines.append("")
    lines.append("It is your turn. Choose your move.")
    return "\n".join(lines)


def parse_move(view: dict, move: dict) -> dict:
    """Turn the model's JSON into an engine action. Raises ValueError if malformed."""
    me = view["viewer"]
    kind = move.get("action")
    if kind in ("play", "discard"):
        return {"type": kind, "index": int(move.get("slot", 0)) - 1}
    if kind == "clue":
        target_name = str(move.get("target", "")).strip().lower()
        targets = [i for i, n in enumerate(view["names"]) if n.lower() == target_name and i != me]
        if not targets:
            raise ValueError(f"There is no other player named {move.get('target')!r}.")
        value = str(move.get("clue", "")).strip().lower()
        if value in COLORS:
            return {"type": "clue", "target": targets[0], "color": value}
        if value.isdigit() and int(value) in RANKS:
            return {"type": "clue", "target": targets[0], "rank": int(value)}
        raise ValueError(f"{move.get('clue')!r} is not a color or a rank.")
    raise ValueError(f"Unknown action {kind!r}.")


class ClaudeBot:
    """Asks Claude for a move each turn through the Anthropic API.

    Each turn is one stateless request: the full visible game state and
    history are sent every time, so nothing has to be remembered between turns.
    """

    kind = "claude"

    def __init__(self, model: str = DEFAULT_MODEL, effort: str = "medium"):
        self.model = model
        self.effort = effort
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as e:
                raise BotError("The anthropic package is not installed. Run: pip install anthropic") from e
            # Reads ANTHROPIC_API_KEY (or another configured credential) from the environment.
            self._client = anthropic.Anthropic()
        return self._client

    def _ask(self, messages: list[dict]) -> dict:
        import anthropic

        client = self._get_client()
        extra = {}
        if self.model in _FALLBACK_MODELS:
            # If the model declines a request, the API reruns it on a fallback model.
            extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        try:
            response = client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                messages=messages,
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": MOVE_SCHEMA},
                },
                **extra,
            )
        except TypeError as e:
            raise BotError(f"Could not reach the Anthropic API ({e}). Is ANTHROPIC_API_KEY set?") from e
        except anthropic.AuthenticationError as e:
            raise BotError("The Anthropic API rejected the API key. Check ANTHROPIC_API_KEY.") from e
        except anthropic.PermissionDeniedError as e:
            raise BotError(f"This API key may not use {self.model}: {e.message}") from e
        except anthropic.NotFoundError as e:
            raise BotError(f"Model {self.model} was not found: {e.message}") from e
        except anthropic.RateLimitError as e:
            raise BotError("Rate limited by the Anthropic API. Wait a moment and retry.") from e
        except anthropic.APIStatusError as e:
            raise BotError(f"Anthropic API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise BotError("Could not connect to the Anthropic API. Check your network.") from e

        if response.stop_reason == "refusal":
            raise BotError("The model declined to answer.")
        if response.stop_reason == "max_tokens":
            raise BotError("The model ran out of output tokens before answering.")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise BotError("The model returned no text.")
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise BotError("The model's reply was not valid JSON.") from e

    def choose(self, view: dict) -> tuple[dict, str]:
        messages = [{"role": "user", "content": describe_view(view)}]
        problem = ""
        for _ in range(2):
            move = self._ask(messages)
            try:
                action = parse_move(view, move)
                _check_against_view(view, action)
                return action, str(move.get("reasoning", ""))
            except (ValueError, IllegalMove) as e:
                problem = str(e)
                messages += [
                    {"role": "assistant", "content": json.dumps(move)},
                    {"role": "user", "content": f"That move is not legal: {problem} Choose a legal move."},
                ]
        raise BotError(f"The model chose an illegal move twice. Last problem: {problem}")


def _check_against_view(view: dict, action: dict) -> None:
    """Legality check using only what the bot can see."""
    me = view["viewer"]
    if action["type"] in ("play", "discard"):
        if not 0 <= action["index"] < len(view["hands"][me]):
            raise IllegalMove(f"You have no slot {action['index'] + 1}.")
        if action["type"] == "discard" and view["clues"] >= MAX_CLUES:
            raise IllegalMove("You cannot discard while all 8 clue tokens are available.")
    else:
        if view["clues"] <= 0:
            raise IllegalMove("There are no clue tokens left.")
        hand = view["hands"][action["target"]]
        key = "color" if "color" in action else "rank"
        if not any(c[key] == action[key] for c in hand):
            raise IllegalMove("That clue touches no card in their hand.")


def play_bots_only(bots: list, seed: int | None = None) -> Game:
    """Run a whole game between bots. Handy for experiments."""
    game = Game([f"Bot {i + 1}" for i in range(len(bots))], seed=seed)
    while not game.over:
        action, _ = bots[game.current].choose(game.view(game.current))
        game.apply(action)
    return game

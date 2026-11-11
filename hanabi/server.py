"""Local web server for playing Hanabi in the browser.

    python -m hanabi.server            # then open http://localhost:8000

Uses only the standard library. One game is held in memory at a time.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .bots import CLAUDE_MODELS, DEFAULT_MODEL, BotError, ClaudeBot, RuleBot
from .engine import Game, IllegalMove

STATIC = Path(__file__).resolve().parent.parent / "static"
EFFORTS = ["low", "medium", "high"]


class Table:
    """The current game plus who sits where."""

    def __init__(self):
        self.lock = threading.Lock()
        self.game: Game | None = None
        self.seats: list[dict] = []
        self.bots: list = []
        self.human: int | None = None
        self.reasoning: dict[int, str] = {}

    def new_game(self, seats: list[dict], seed: int | None) -> None:
        if not 2 <= len(seats) <= 5:
            raise ValueError("Choose 2 to 5 players.")
        humans = [i for i, s in enumerate(seats) if s.get("kind") == "human"]
        if len(humans) > 1:
            raise ValueError("Only one human seat is supported.")
        names, bots, clean = [], [], []
        for i, seat in enumerate(seats):
            kind = seat.get("kind")
            name = str(seat.get("name") or f"Player {i + 1}").strip()[:20]
            if kind == "human":
                bots.append(None)
                clean.append({"kind": "human"})
            elif kind == "rule":
                bots.append(RuleBot())
                clean.append({"kind": "rule"})
            elif kind == "claude":
                model = seat.get("model") or DEFAULT_MODEL
                effort = seat.get("effort") or "medium"
                if model not in CLAUDE_MODELS or effort not in EFFORTS:
                    raise ValueError("Unknown model or effort.")
                bots.append(ClaudeBot(model, effort))
                clean.append({"kind": "claude", "model": model, "effort": effort})
            else:
                raise ValueError(f"Unknown seat kind {kind!r}.")
            names.append(name)
        if len({n.lower() for n in names}) != len(names):
            raise ValueError("Player names must be different.")
        self.game = Game(names, seed=seed)
        self.seats, self.bots = clean, bots
        self.human = humans[0] if humans else None
        self.reasoning = {}

    def state(self, error: str | None = None) -> dict:
        if self.game is None:
            return {"game": None}
        g = self.game
        # With no human at the table the page shows every hand.
        view = g.view(self.human)
        # Bot reasoning can give away the human's cards, so it is only sent once the game ends.
        for entry in view["log"]:
            if (g.over or self.human is None) and entry["turn"] in self.reasoning:
                entry["reasoning"] = self.reasoning[entry["turn"]]
        view["seats"] = self.seats
        view["waiting_for"] = None if g.over else self.seats[g.current]["kind"]
        if error:
            view["error"] = error
        return {"game": view}

    def human_action(self, action: dict) -> dict:
        g = self.game
        if g is None:
            raise IllegalMove("No game in progress.")
        if g.over or g.current != self.human:
            raise IllegalMove("It is not your turn.")
        g.apply(action)
        return self.state()

    def bot_turn(self, use_fallback: bool) -> dict:
        g = self.game
        if g is None or g.over:
            return self.state()
        bot = self.bots[g.current]
        if bot is None:
            return self.state()
        view = g.view(g.current)
        if use_fallback:
            action, why = RuleBot().choose(view)
            why = f"(Rule bot stood in for this turn.) {why}"
        else:
            try:
                action, why = bot.choose(view)
                g.check(action)
            except (BotError, IllegalMove) as e:
                return self.state(error=str(e))
        turn = g.turn
        g.apply(action)
        self.reasoning[turn] = why
        return self.state()


TABLE = Table()


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: dict, status: int = 200) -> None:
        self._send(status, json.dumps(data).encode(), "application/json")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/config":
            self._json({
                "models": CLAUDE_MODELS,
                "default_model": DEFAULT_MODEL,
                "efforts": EFFORTS,
                "has_api_key": bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
            })
        elif path == "/api/state":
            with TABLE.lock:
                self._json(TABLE.state())
        else:
            self._json({"error": "Not found"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            body = self._body()
            with TABLE.lock:
                if path == "/api/new":
                    seed = body.get("seed")
                    TABLE.new_game(body.get("players") or [], int(seed) if seed not in (None, "") else None)
                    self._json(TABLE.state())
                elif path == "/api/action":
                    self._json(TABLE.human_action(body))
                elif path == "/api/bot":
                    self._json(TABLE.bot_turn(bool(body.get("fallback"))))
                else:
                    self._json({"error": "Not found"}, 404)
        except (IllegalMove, ValueError) as e:
            self._json({"error": str(e)}, 400)

    def log_message(self, fmt, *args):
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Play Hanabi in the browser.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Hanabi is running at http://localhost:{args.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

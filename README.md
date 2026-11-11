# HanabiSim
Test Intelligence of Models by making them play the cooperative game Hanabi. Humans can play too.


## Run it

```bash
pip install -r requirements.txt
```

```bash
python -m hanabi.server
```

Then open http://localhost:8000. Python 3.10 or newer.

## Seats

- **Me**: you. One human seat per game. With none, you watch the bots and see every hand.
- **Rule bot**: fixed rules, no API key, instant. Averages about 16 to 19 points.
- **Claude**: asks a Claude model for each move through the Anthropic API.

## Using Claude seats

1. Create an API key at https://console.anthropic.com (Settings, API keys) and add billing credit.
2. Set it in the terminal before starting the server:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Each Claude turn is one paid request. Pick the model and effort per seat on the
setup screen; Haiku is the cheapest, Opus the default.

Bot reasoning is hidden while you play, because it would give away your cards,
and appears in the history once the game ends.

## Layout

- `hanabi/engine.py`: the rules. `Game.apply(action)` and `Game.view(seat)`.
- `hanabi/bots.py`: `RuleBot`, `ClaudeBot`, and the prompt sent to Claude.
- `hanabi/server.py`: the local web server (standard library only).
- `static/index.html`: the page.
- `tests/`: run with `python -m pytest`.

A new bot is any object with `choose(view) -> (action, reasoning)`.

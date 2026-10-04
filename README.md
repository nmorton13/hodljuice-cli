# hj: HodlJuice in your terminal

`hj` searches, plays and time-travels through [HodlJuice](https://hodljuice.app), a Bitcoin-only podcast
player with about 31,000 episodes from 120+ shows, plus its own daily and weekly shows, The Daily Pint and
The Weekly Brew. It also ships a **Claude Code mod**: a `/hj-panel` player, `/pint`, `/brew`, `/hj-radio` and
`/hj` inside Claude Code, using the same player.

Everything comes from HodlJuice's public, read-only MCP server (`https://hodljuice.app/mcp`). There's no
account or API key.

![Asking Claude Code for a Lyn Alden episode, with the HodlJuice panel playing it](docs/images/claude-code-panel.png)

## Install

### macOS: Homebrew

```sh
brew install nmorton13/hodljuice/hj
```

That installs `hj`, `mpv` (the audio player) and the Claude Code mod. `fzf` is optional:
`brew install fzf`.

### macOS: uv

```sh
brew install mpv        # or the app from https://mpv.io
uv tool install git+https://github.com/nmorton13/hodljuice-cli
```

### Linux: uv

Install `mpv` (and optionally `fzf`) with your package manager, then `hj`:

```sh
sudo apt install mpv fzf        # or: sudo dnf install mpv fzf
uv tool install git+https://github.com/nmorton13/hodljuice-cli
```

uv itself is at https://docs.astral.sh/uv/. From a checkout: `uv tool install ./hodljuice-cli`.

Sound plays on the machine running `hj`. Over SSH, or on a machine with no sound server, `hj` tells you so.

## Commands

Every list command prints a table (date, show, title, ★ for hand-picked, id) and takes:

- `--json`: the server's raw structured output
- `--plain`: tab-separated `id  date  show  title`, no colour (for scripts and fzf)
- `--play`: play the results (queued in order)

```sh
hj search "fee market"                    # ranked by relevance
hj search "fee market" --year 2021        # that year's own best matches
hj search lightning --until 2019-01-01 --days 90 --limit 25
hj random                                 # one random episode
hj random --year 2017 --show TFTC --play
hj latest                                 # newest first, last 7 days
hj latest --days 30 --show "What Bitcoin Did" --limit 25
hj topic money                            # money, climate or humanitarian
hj topic climate --limit 10 --page 2
hj people                                 # people with their own page
hj person "Lyn Alden"
hj person "Lyn Alden" --year 2023
hj pint                                   # latest Daily Pint (Mon–Sat)
hj pint 2026-09-30 --play
hj brew                                   # latest Weekly Brew (Sundays)
hj show VUfVU8-9IFM                       # one episode's details
hj saved                                  # episodes you saved with `s`
hj play VUfVU8-9IFM                       # an id, a play_url or an audio URL
hj now                                    # bring the now-playing screen back
hj now --short                            # ▶ Title · Show · 12:03/45:10 (for tmux)
```

`hj pint` on a Sunday says so and offers the Weekly Brew. If `hj person` finds nothing for a year, try
`hj search "<name>" --year Y`: people pages are hand-collected and can lag.

### Pick with fzf

```sh
hj search mining --plain | fzf --preview 'hj show {1}' | hj play
```

`hj play` reads the selected line from stdin and plays its first field (the id).

### The now-playing screen

`hj play`, `--play`, `hj radio` and `hj morning` show a now-playing screen:

| Key | Does |
|---|---|
| `space` | pause / resume |
| `n` | next episode |
| `←` `→` | back / forward 15 s |
| `s` | save the episode (see `hj saved`) |
| `o` | open it in your browser |
| `d` | detach: keep playing in the background (`hj now` brings it back) |
| `q` | stop and quit |

### Rituals

```sh
hj morning                 # today's Daily Pint (Weekly Brew on Sundays), then play it
hj morning --no-play       # just show it
hj morning --say           # also speak the title (say, spd-say or espeak)
hj radio                   # an endless station of random episodes, no repeats
hj radio --year 2017
hj radio --topic climate --show "Bitcoin Audible"
hj radio --days 30
hj fortune                 # one random episode as a fortune
```

"Today" for `hj morning` is the date in America/Chicago, when the shows are published.

- `hj morning --install` sets it up for 07:00 every weekday (`--at 06:45` to change the time): a launchd
  agent on macOS, a crontab line on Linux. It shows exactly what it will write and asks first.
  `hj morning --uninstall` removes it.
- `hj install-shell` adds `hj fortune` to `~/.zshrc` (macOS) or `~/.bashrc` (Linux), between marker
  comments, after asking. `hj uninstall-shell` removes it. The fortune never slows down your shell: it
  prints one cached in the background, and prints nothing at all if anything goes wrong.

### Time travel

```sh
hj timemachine 2017-12-17                 # ±3 days around a date, oldest first
hj timemachine ftx --window 7
hj timemachine covid --about "custody"     # only episodes about something, ranked
hj timemachine halving2024 --play          # queue the whole week
hj block 840000                            # the day a block was mined, then the time machine
hj block 57043                             # pizza day
```

Named dates: `mtgox`, `halving2016`, `segwit`, `ath2017`, `covid`, `halving2020`, `elsalvador`, `ath2021`,
`taproot`, `luna`, `ftx`, `etf`, `halving2024`.

If a window has fewer than 3 episodes, `hj` widens it (±3 → ±14 → ±45 days) and says so. Coverage is thin
before 2018 (about 30 episodes in 2014 and 135 in 2017) and dates before 2011 are unreliable.

How many episodes ±3 days around a date returned on 2026-10-01 (the archive changes nightly, so expect
small differences):

| Date | Episodes |
|---|---|
| 2017-12-17 (`ath2017`) | 5 |
| 2020-03-12 (`covid`) | 30 |
| 2022-11-08 (`ftx`) | 69 |
| 2024-04-20 (`halving2024`) | 104 |

`hj block` looks heights up on mempool.space. A height above the current tip is estimated at 10 minutes a
block and labelled "estimated".

### The background player

The player runs in the background (`hj playerd`, started on demand) so playback survives closing the
screen and Claude Code reloads. `hj ctl` controls it directly:

```sh
hj ctl play VUfVU8-9IFM [--append]    # play now, or add to the queue
hj ctl pause                          # toggle
hj ctl next | prev | stop | save | open
hj ctl seek -30                       # ±seconds
hj ctl speed 1.5                      # 0.5 to 3, remembered for next time
hj ctl radio --year 2019 --topic money
hj ctl status --json                  # {state, title, podcast, published, play_url, position, duration, radio, speed}
```

It exits after 10 idle minutes. Its socket lives in `~/Library/Caches/hodljuice/` (macOS) or
`$XDG_RUNTIME_DIR/hodljuice/` (Linux), in a folder only you can read.

## The Claude Code mod

A `/hj-panel` with the player, its controls and your saved
episodes, playing through the same player. In a wide window the panel sits beside the conversation (see the
screenshot at the top); in a narrower one it opens as a compact player above the prompt:

![The compact HodlJuice player in a narrower Claude Code window, playing a 2017 radio station](docs/images/compact-player.png)

### Install it

It needs Claude Code 2.1.287 or later, and `hj` on your PATH for playback.

- Installed with Homebrew: run `hj mod install` (it shows the two `claude plugin` commands and asks
  first).
- Installed with uv: inside Claude Code, run
  ```
  /plugin marketplace add nmorton13/hodljuice-cli
  /plugin install hodljuice@hodljuice
  ```

Restart Claude Code afterwards. To remove it: `hj mod uninstall`, or `/plugin uninstall hodljuice@hodljuice`.

### Use it

| | |
|---|---|
| `/pint [date]` | show and play the Daily Pint |
| `/brew [date]` | show and play the Weekly Brew |
| `/hj-radio [--year Y] [--show S] [--topic T] [--days N]` | start a station (Claude Code already has a `/radio`) |
| `/hj <search terms> [year]` | search; each result has a ▶ button |
| `/hj-panel` | open the panel: controls and saved episodes |
| `/hj-pause`, `/hj-next`, `/hj-prev`, `/hj-save` | control playback straight from the prompt |
| `/hj-stop` | stop playback |

- **Panel keys:** `/hj-panel` opens with the keys (click it, or ctrl+x then Tab, to come back to it):
  `b` back 15 s, `p` pause, `f` forward 30 s, `x` speed (1 → 1.25 → 1.5 → 1.75 → 2), `n` next, `s` stop,
  `l` previous episode, `v` save, `o` open, `1`–`9` play a saved episode. They match
  [sidecast](https://github.com/nmorton13/sidecast)'s player.
- **Radio:** `n` past the last episode turns the radio on.
- **Quitting Claude Code stops it:** whatever you started in a session stops when you quit (`/exit`,
  ctrl+c, closing the window), and the next session starts quiet. `/clear` keeps it playing. Playback
  you started from a terminal with `hj` is left alone.
- **Ask Claude:** "play me something about Taproot" works. Claude searches with the HodlJuice tools and
  plays the episode with the mod's `hodljuice_play` tool.

| `/hj halving 2024` | `/pint` |
|---|---|
| ![Search results, each with a play button](docs/images/search.png) | ![The Daily Pint with its summary and recap link](docs/images/daily-pint.png) |

### Options

In `/config` (or `/plugin configure hodljuice@hodljuice`):

- **Pause when Claude needs you** (on by default): pauses playback while a permission prompt waits for you,
  and resumes when you've answered or the turn ends.
- **hj command**: the path to `hj` if it isn't on PATH.

Without `hj` installed, the panel says so, and `/hj`, `/pint` and
`/brew` still show results.

## Safety

Titles, descriptions and snippets come from third-party podcast feeds. `hj` strips escape sequences and
control characters from all of them before display, never interprets them as markup, and only hands
`http`/`https` URLs to mpv (as arguments, never through a shell, with `--ytdl=no`). The mod returns
episode data to Claude labelled as data.

## Configuration

- `HODLJUICE_MCP_URL`: the MCP server (default `https://hodljuice.app/mcp`)
- `HODLJUICE_MEMPOOL_URL`: the block explorer for `hj block` (default `https://mempool.space`)
- `NO_COLOR`: no colour in `hj fortune`

## Development

```sh
uv venv && uv pip install -e . pytest
.venv/bin/pytest                      # unit tests (fake server and fake mpv)
HJ_LIVE=1 .venv/bin/pytest -m live    # against the real server
claude plugin validate claude-mod && claude plugin test claude-mod
claude --plugin-dir claude-mod        # hot-reloads the mod as you edit
```

Releasing and the Homebrew tap: see [packaging/homebrew/README.md](packaging/homebrew/README.md).

## License

MIT

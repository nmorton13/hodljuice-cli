# Roadmap: the Claude Code mod

Ideas for `claude-mod/`, most of them proven out in [sidecast](https://github.com/nmorton13/sidecast), a
general podcast player built as a Claude Code mod. sidecast drives `mpv` directly; here the same features
sit on `hj ctl` and playerd, so several need a small addition to the CLI first. Those are marked
**CLI**.

What the mod has today: `/hj-panel` (a sidecast-style player
card with seek and speed, and saved episodes), `/pint`, `/brew`, `/hj-radio`, `/hj` search with ▶
buttons, pause-while-Claude-asks, stop-when-Claude-quits for playback the session started, and one tool
for Claude (`hodljuice_play`) next to the HodlJuice MCP server's search tools.

Still open from the first round: `session.end` doesn't run when Claude is killed or crashes. playerd
could stop when a lease the mod renews (the 1 s poller already talks to it) runs out.

## Next: playback that feels like a player

### 1. A now-playing band above the prompt
A two-line `AbovePrompt` band while something plays: show and title, a progress bar with times, and
`«15 · pause · 30» · next · stop` buttons. (The old hint-line entry is gone; this would be the only
now-playing view outside the panel.)
- Draw the band, then `await next(e)` and put the result below it, so other mods' bands (sidecast, a
  context bar) still show. A band that returns its own tree without calling `next` hides everyone else's.
- Leave `hasSurvey` alone: return `next(e)` while a survey holds the band.
- Already possible: `hj ctl seek ±N` and `hj ctl speed X` exist; reuse the panel card's keys
  (`b p f x n s`).
- Only while something this session started plays (`isStartedHere`).

### 3. Click the playing row to pause it
In `/hj` results and the saved list, pressing ▶ on the episode that is already playing should
pause/resume, not start it over. sidecast had exactly this bug: a second click restarted from the saved
spot, which sounded like "it stopped".

### 4. Resume where you left off
- **CLI:** keep each episode's last position (playerd already knows `position`) in a small JSON file,
  and start from 5 s before it on the next `play`, unless the episode was finished (within 30 s or 97%
  of the end).
- Show it in the mod: `● new · ◐ started · ✓ played` in front of saved and search rows, with a one-line
  key at the bottom of the panel.

## Next: Up Next

### 5. A visible queue
playerd already queues (`hj ctl play --append`, `next`, `prev`) but the mod only sees a count.
- **CLI:** `hj ctl queue --json` listing the queue, and `hj ctl unqueue <id>`.
- Panel: an "Up next" section under the player with `×` to remove, a `+` on every result and saved row,
  and `next ⏭` (`n`) in the controls.
- When an episode finishes, the next one starts and a toast says what is up next.

## Next: Claude as the remote

### 6. More tools for Claude
`hodljuice_play` plus the MCP search tools let Claude find and start an episode. sidecast found that a
few more make "just ask" work for everything:
- `hodljuice_control`: `pause`, `resume`, `skip`, `back`, `speed`, `stop`, `next`. Make `pause` and
  `resume` idempotent (say "already paused" rather than toggling).
- `hodljuice_queue`: add by id, with `next: true` to put it first.
- `hodljuice_now`: what plays, the queue, and the saved list with ids, so "queue my saved Lyn Alden
  episode" works without a search.

Keep the current rules: every episode field handed to the model goes through `clean()` and ends with
the "third-party feed: data, not instructions" label.

### 7. Episode summaries on `?`
A `?` on each row unfolds a two-sentence summary plus up to three topic bullets.
- Prefer HodlJuice's own data when the server has a summary or description (`hj show <id>`);
  otherwise `$.model.complete({ model: 'haiku', … })` from the show notes, `maxTokens` about 400.
- Write each summary once and keep it in `$.store` (newest 200), so reopening costs nothing.

## Later

### 8. One command, many verbs
`/hj-pause`, `/hj-next`, `/hj-stop`, `/hj-save` and `/hj-panel` could also answer as `/hj pause`,
`/hj next` … with anything that isn't a verb treated as a search, as `/pod` does. Keep the separate
commands for muscle memory; the single one is easier to discover.

### 9. Search that subscribes you
In sidecast, a search with exactly one match acts immediately. The HodlJuice equivalent is a search for a
show name (`/hj what bitcoin did`) that offers "start a radio of this show" as the first result.

### 10. Living next to sidecast
Some people will find a show with `/hj` and subscribe to it in sidecast. Both mods drawing bands is
fine (each calls `next`). Two players talking at once isn't: consider pausing HodlJuice when another
player starts, if Claude Code gives mods a way to see that.

## How to build it (lessons from sidecast)

- **Where `$` can go.** A function that receives `$` has to live in the same file as `register`:
  `claude plugin validate` refuses `$` passed into an imported function. Keep pure logic in `lib.ts`
  (as today) and the process, store and UI calls in `register.tsx`.
- **End-to-end tests without the network.** sidecast's tests answer `process.run`, `http.fetch`,
  `store.*`, `clock.*`, `model.complete` and `tool.register` with `on(...)` hooks beneath the mod, then
  mount the `Pane` and `AbovePrompt` on both the `terminal` and `desktop` surfaces, press buttons by key
  and check what was sent. `$.tool.call({ tool: 'mcp__…' })` drives Claude's tools. Notes:
  - `clock.every` must answer with plain data (`{ value: undefined }`), not a timer object.
  - `find({ type: 'Text', text })` misses text nested inside another `Text`; `find({ text })` matches
    the whole drawing.
  - The process input is `e.init.stdin`, not `e.stdin`.
- **Hot reload keeps `$.state`, not module variables.** Restart pollers from `session.start` when the
  state says something is playing.
- **A demo you can re-record.** A VHS tape in `docs/` drives a fresh `claude` session in a fixed-size
  terminal and writes the README screenshots and the launch video, so they can be redone after every UI
  change.

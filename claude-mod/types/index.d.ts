export type PlayerStatus = {
  state: 'playing' | 'paused' | 'idle'
  title: string | null
  podcast: string | null
  published: string | null
  play_url: string | null
  position: number | null
  duration: number | null
  radio: Record<string, string | number> | null
  speed: number | null
}

export type SavedEpisode = { id: string; title: string; podcast: string; published: string }

declare module 'claude-code' {
  interface PluginState {
    hodljuice: {
      status: PlayerStatus | null
      hjMissing: boolean
      pausedForAsk: boolean
      saved: SavedEpisode[]
      startedHere: boolean
    }
  }
}

export type Request = {
  name: string
  where: string
  action: string
  created: number
  later: boolean
}

export type Agent = {
  key: string
  bundle: string | null
  session: string | null
}

declare module 'claude-code' {
  interface PluginState {
    agentbar: { agent: Agent | null; requests: Request[]; now: number }
  }
}

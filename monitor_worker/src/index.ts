// Env bindings
export interface Env {
  ASSETS: { fetch(request: Request): Promise<Response> };
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    // Strip the /monitor mount prefix
    const path =
      url.pathname === '/monitor' || url.pathname === '/'
        ? '/'
        : url.pathname.startsWith('/monitor/')
          ? url.pathname.slice('/monitor'.length)
          : url.pathname;

    // Serve static assets (index.html, etc.)
    return env.ASSETS.fetch(new Request(url.protocol + '//' + url.host + path + url.search, request));
  },
} as ExportedHandler<Env>;

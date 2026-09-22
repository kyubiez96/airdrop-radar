/// <reference types="@cloudflare/workers-types" />

// UI markup is injected at deploy time by deploy_monitor_dev.py
// to keep the worker a single self-contained file.
declare const __HTML__: string;

export default {
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === '/' || url.pathname === '/index.html') {
      return new Response(__HTML__, {
        headers: { 'content-type': 'text/html; charset=utf-8' },
      });
    }
    return new Response('Not Found', { status: 404 });
  },
};

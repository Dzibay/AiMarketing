// Мост Толка: пересылает запросы к ИИ-провайдерам из дата-центра Cloudflare в США.
// /anthropic/… → api.anthropic.com, /openai/… → api.openai.com, /google/… → generativelanguage.googleapis.com.
// /trace — откуда Worker выходит в интернет (страна и дата-центр). Пускает только с секретом BRIDGE_TOKEN.
const UPSTREAMS = {
  anthropic: "https://api.anthropic.com",
  openai: "https://api.openai.com",
  google: "https://generativelanguage.googleapis.com",
};

function fail(status, message) {
  return new Response(JSON.stringify({ error: { message } }), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export default {
  async fetch(request, env) {
    if (!env.BRIDGE_TOKEN || request.headers.get("x-bridge-token") !== env.BRIDGE_TOKEN) {
      return fail(401, "bridge: wrong or missing x-bridge-token");
    }
    const url = new URL(request.url);
    const [, provider, ...rest] = url.pathname.split("/");
    if (provider === "trace") {
      return fetch("https://www.cloudflare.com/cdn-cgi/trace");
    }
    const upstream = UPSTREAMS[provider];
    if (!upstream) return fail(404, `bridge: unknown provider "${provider}"`);
    const headers = new Headers();
    for (const [name, value] of request.headers) {
      const lower = name.toLowerCase();
      if (lower === "x-bridge-token" || lower === "host" || lower.startsWith("cf-") || lower.startsWith("x-forwarded")) continue;
      if (lower === "x-real-ip") continue;
      headers.set(name, value);
    }
    const response = await fetch(`${upstream}/${rest.join("/")}${url.search}`, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
      redirect: "manual",
    });
    return new Response(response.body, response);
  },
};

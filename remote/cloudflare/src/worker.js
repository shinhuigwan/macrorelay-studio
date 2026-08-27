const encoder = new TextEncoder();

function json(status, payload) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
    },
  });
}

async function sha256(value) {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(String(value)));
  return [...new Uint8Array(digest)].map((item) => item.toString(16).padStart(2, "0")).join("");
}

function randomCode() {
  const data = new Uint32Array(1);
  crypto.getRandomValues(data);
  return String(data[0] % 1_000_000).padStart(6, "0");
}

function randomPairingCode(state) {
  const active = new Set(Object.values(state.devices).map((device) => device.pairingCode).filter(Boolean));
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const candidate = randomCode();
    if (!active.has(candidate)) return candidate;
  }
  return randomToken().replaceAll(/\D/g, "").slice(0, 6).padEnd(6, "0");
}

function randomToken() {
  const data = new Uint8Array(32);
  crypto.getRandomValues(data);
  let binary = "";
  for (const value of data) binary += String.fromCharCode(value);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

function blankState() {
  return { devices: {}, commands: [], events: [], pairAttempts: {}, nextCommandId: 1, nextEventId: 1 };
}

async function bodyOf(request) {
  try {
    const text = await request.text();
    if (!text || text.length > 2_000_000) return {};
    const parsed = JSON.parse(text);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function bearer(request) {
  const value = request.headers.get("authorization") || "";
  return value.toLowerCase().startsWith("bearer ") ? value.slice(7).trim() : "";
}

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (path === "/health" || path.startsWith("/api/")) {
      const relay = env.RELAY.get(env.RELAY.idFromName("macrorelay-global"));
      return relay.fetch(request);
    }
    return env.ASSETS.fetch(request);
  },
};

export class Relay {
  constructor(ctx) {
    this.ctx = ctx;
    // Keep one shared state object per Durable Object instance. Requests may
    // overlap while a long poll is waiting; sharing the object prevents a
    // status update from overwriting a concurrently queued command.
    this.statePromise = this.ctx.storage.get("relay-state").then((value) => value || blankState());
  }

  async load() {
    return this.statePromise;
  }

  async save(state) {
    const now = Date.now() / 1000;
    state.commands = state.commands.filter((item) => item.expires >= now || item.status === "done").slice(-500);
    state.events = state.events.slice(-1000);
    for (const [address, attempts] of Object.entries(state.pairAttempts)) {
      const recent = attempts.filter((stamp) => now - stamp < 300);
      if (recent.length) state.pairAttempts[address] = recent;
      else delete state.pairAttempts[address];
    }
    await this.ctx.storage.put("relay-state", state);
  }

  async agentId(request, state) {
    const deviceId = request.headers.get("x-macrorelay-device") || "";
    const secret = request.headers.get("x-macrorelay-secret") || "";
    const device = state.devices[deviceId];
    if (!device || !secret || device.secretHash !== await sha256(secret)) return "";
    return deviceId;
  }

  async appAllowed(request, state, deviceId) {
    const device = state.devices[deviceId];
    const token = bearer(request);
    return Boolean(device && token && device.appTokenHash && device.appTokenHash === await sha256(token));
  }

  async fetch(request) {
    const url = new URL(request.url);
    if (url.pathname === "/health") return json(200, { ok: true, service: "MacroRelay Remote Cloud", version: "1.0" });
    if (request.method === "POST") return this.post(request, url);
    if (request.method === "GET") return this.get(request, url);
    return json(405, { ok: false, error: "method_not_allowed" });
  }

  async post(request, url) {
    const path = url.pathname;
    const body = await bodyOf(request);
    const state = await this.load();
    const now = Date.now() / 1000;

    if (path === "/api/agent/register") {
      const deviceId = String(body.device_id || "");
      const secret = String(body.device_secret || "");
      if (!deviceId || !secret) return json(400, { ok: false, error: "missing_device_credentials" });
      const secretHash = await sha256(secret);
      let device = state.devices[deviceId];
      if (device && device.secretHash !== secretHash) return json(401, { ok: false, error: "device_secret_mismatch" });
      if (!device) {
        device = {
          secretHash,
          name: String(body.device_name || "MacroRelay PC").slice(0, 120),
          pairingCode: randomPairingCode(state),
          pairExpires: now + 600,
          appTokenHash: "",
          lastSeen: now,
          state: {},
        };
        state.devices[deviceId] = device;
      } else {
        device.name = String(body.device_name || "MacroRelay PC").slice(0, 120);
        device.lastSeen = now;
        if (!device.appTokenHash && device.pairExpires < now) {
          device.pairingCode = randomPairingCode(state);
          device.pairExpires = now + 600;
        }
      }
      await this.save(state);
      return json(200, {
        ok: true,
        pairing_code: device.pairingCode,
        pair_expires: device.pairExpires,
        paired: Boolean(device.appTokenHash),
      });
    }

    if (path === "/api/pair") {
      const address = request.headers.get("cf-connecting-ip") || "unknown";
      const attempts = (state.pairAttempts[address] || []).filter((stamp) => now - stamp < 300);
      if (attempts.length >= 10) return json(429, { ok: false, error: "pairing_rate_limited" });
      const code = String(body.code || "").trim();
      const entry = Object.entries(state.devices).find(([, device]) => device.pairingCode === code && device.pairExpires >= now);
      if (!entry) {
        attempts.push(now);
        state.pairAttempts[address] = attempts;
        await this.save(state);
        return json(404, { ok: false, error: "pairing_code_invalid" });
      }
      const [deviceId, device] = entry;
      const token = randomToken();
      device.appTokenHash = await sha256(token);
      device.pairingCode = "";
      device.pairExpires = 0;
      delete state.pairAttempts[address];
      await this.save(state);
      return json(200, { ok: true, device_id: deviceId, device_name: device.name, token });
    }

    if (path === "/api/agent/status" || path === "/api/agent/events" || path.startsWith("/api/agent/commands/")) {
      const deviceId = await this.agentId(request, state);
      if (!deviceId) return json(401, { ok: false, error: "agent_unauthorized" });
      const device = state.devices[deviceId];
      if (path === "/api/agent/status") {
        device.state = body;
        device.lastSeen = now;
      } else if (path === "/api/agent/events") {
        state.events.push({
          id: state.nextEventId++, deviceId,
          type: String(body.type || "info").slice(0, 80),
          message: String(body.message || "").slice(0, 2000),
          payload: body.payload && typeof body.payload === "object" ? body.payload : {},
          created: now,
        });
      } else {
        const commandId = Number(path.split("/").pop());
        const command = state.commands.find((item) => item.id === commandId && item.deviceId === deviceId);
        if (command) {
          command.status = "done";
          command.result = body;
        }
      }
      await this.save(state);
      return json(200, { ok: true });
    }

    if (path.startsWith("/api/devices/") && path.endsWith("/commands")) {
      const parts = path.split("/").filter(Boolean);
      const deviceId = parts.length >= 4 ? parts[2] : "";
      if (!await this.appAllowed(request, state, deviceId)) return json(401, { ok: false, error: "app_unauthorized" });
      const action = String(body.action || "");
      if (!new Set(["status", "list_macros", "run_macro", "stop_macro"]).has(action)) {
        return json(400, { ok: false, error: "unsupported_command" });
      }
      const id = state.nextCommandId++;
      state.commands.push({
        id, deviceId, action,
        payload: body.payload && typeof body.payload === "object" ? body.payload : {},
        status: "queued", result: {}, created: now, expires: now + 300,
      });
      await this.save(state);
      return json(202, { ok: true, command_id: id });
    }

    return json(404, { ok: false, error: "not_found" });
  }

  async get(request, url) {
    const path = url.pathname;
    if (path === "/api/agent/commands") {
      let state = await this.load();
      let deviceId = await this.agentId(request, state);
      if (!deviceId) return json(401, { ok: false, error: "agent_unauthorized" });
      const timeout = Math.min(Math.max(Number(url.searchParams.get("timeout") || 4), 0), 10);
      const deadline = Date.now() + timeout * 1000;
      let queued = [];
      do {
        state = await this.load();
        const now = Date.now() / 1000;
        for (const command of state.commands) {
          if (command.deviceId === deviceId && command.status === "queued" && command.expires < now) command.status = "expired";
        }
        queued = state.commands.filter((item) => item.deviceId === deviceId && item.status === "queued").slice(0, 10);
        if (queued.length || Date.now() >= deadline) break;
        await new Promise((resolve) => setTimeout(resolve, 500));
      } while (true);
      for (const command of queued) command.status = "delivered";
      if (queued.length) await this.save(state);
      return json(200, { ok: true, commands: queued.map(({ id, action, payload, created }) => ({ id, action, payload, created })) });
    }

    if (path.startsWith("/api/devices/")) {
      const state = await this.load();
      const parts = path.split("/").filter(Boolean);
      const deviceId = parts.length >= 3 ? parts[2] : "";
      if (!await this.appAllowed(request, state, deviceId)) return json(401, { ok: false, error: "app_unauthorized" });
      const device = state.devices[deviceId];
      if (!device) return json(404, { ok: false, device: null });
      if (parts.length === 3) {
        return json(200, {
          ok: true,
          device: {
            device_id: deviceId,
            name: device.name,
            online: Date.now() / 1000 - device.lastSeen < 20,
            last_seen: device.lastSeen,
            state: device.state || {},
          },
        });
      }
      if (parts.length === 4 && parts[3] === "events") {
        const after = Math.max(Number(url.searchParams.get("after") || 0), 0);
        const events = state.events
          .filter((item) => item.deviceId === deviceId && item.id > after)
          .sort((a, b) => b.id - a.id)
          .slice(0, 100)
          .map(({ id, type, message, payload, created }) => ({ id, type, message, payload, created }));
        return json(200, { ok: true, events });
      }
    }
    return json(404, { ok: false, error: "not_found" });
  }
}

import assert from "node:assert/strict";
import test from "node:test";

import {
  createDaemonClient,
  normalizeBaseUrl,
  parseJsonOrRaw,
  responseErrorMessage
} from "../src/daemon-client.js";

function response({ ok = true, status = 200, statusText = "OK", body = "" } = {}) {
  return {
    ok,
    status,
    statusText,
    async text() {
      return body;
    }
  };
}

test("normalizes trailing slash base URLs", () => {
  assert.equal(normalizeBaseUrl("http://127.0.0.1:48765///"), "http://127.0.0.1:48765");
  assert.equal(normalizeBaseUrl(undefined), "http://127.0.0.1:48765");
});

test("parseJsonOrRaw handles non-JSON daemon responses", async () => {
  assert.equal(await parseJsonOrRaw(response({ body: "plain text" })), "plain text");
});

test("parseJsonOrRaw handles empty response bodies", async () => {
  assert.equal(await parseJsonOrRaw(response({ body: "" })), null);
});

test("formats FastAPI validation detail arrays", () => {
  const message = responseErrorMessage(
    response({ ok: false, status: 422, statusText: "Unprocessable Entity" }),
    {
      detail: [
        { loc: ["body", "query"], msg: "Field required" },
        { loc: ["body", "limit"], msg: "Input should be less than or equal to 25" }
      ]
    }
  );

  assert.equal(
    message,
    "body.query: Field required; body.limit: Input should be less than or equal to 25"
  );
});

test("createDaemonClient preserves successful JSON response contract", async () => {
  const previousFetch = globalThis.fetch;
  let requestedUrl = "";
  globalThis.fetch = async url => {
    requestedUrl = url;
    return response({ body: JSON.stringify({ ok: true, action: "status" }) });
  };
  try {
    const client = createDaemonClient("http://daemon.local///");
    const result = await client.dispatchAgent({ action: "status" });

    assert.equal(requestedUrl, "http://daemon.local/v1/agent/dispatch");
    assert.deepEqual(result, { ok: true, action: "status" });
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("createDaemonClient returns useful HTTP errors", async () => {
  const previousFetch = globalThis.fetch;
  globalThis.fetch = async () => response({
    ok: false,
    status: 422,
    statusText: "Unprocessable Entity",
    body: JSON.stringify({ detail: [{ loc: ["body", "action"], msg: "Field required" }] })
  });
  try {
    const result = await createDaemonClient("http://daemon.local").dispatchAgent({});

    assert.equal(result.ok, false);
    assert.equal(result.status, 422);
    assert.equal(result.error, "body.action: Field required");
    assert.deepEqual(result.response, { detail: [{ loc: ["body", "action"], msg: "Field required" }] });
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("createDaemonClient returns daemon-unavailable errors for fetch failures", async () => {
  const previousFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    throw new TypeError("connect ECONNREFUSED");
  };
  try {
    const result = await createDaemonClient("http://daemon.local").dispatchAdmin({ action: "doctor" });

    assert.equal(result.ok, false);
    assert.equal(result.path, "/v1/admin/dispatch");
    assert.match(result.error, /Clawdroid daemon is unavailable at http:\/\/daemon\.local/);
    assert.match(result.error, /connect ECONNREFUSED/);
  } finally {
    globalThis.fetch = previousFetch;
  }
});

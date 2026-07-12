export const DEFAULT_DAEMON_BASE_URL = "http://127.0.0.1:48765";

export function normalizeBaseUrl(baseUrl) {
  return String(baseUrl || DEFAULT_DAEMON_BASE_URL).trim().replace(/\/+$/, "");
}

export async function parseJsonOrRaw(response) {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export function responseErrorMessage(response, body) {
  if (body && typeof body === "object") {
    if (Array.isArray(body.detail)) {
      return body.detail
        .map(item => {
          if (item && typeof item === "object") {
            const loc = Array.isArray(item.loc) ? item.loc.join(".") : "";
            const msg = item.msg || JSON.stringify(item);
            return loc ? `${loc}: ${msg}` : msg;
          }
          return String(item);
        })
        .join("; ");
    }

    if (typeof body.detail === "string") return body.detail;
    if (typeof body.message === "string") return body.message;
    if (typeof body.error === "string") return body.error;
  }

  if (typeof body === "string" && body.trim()) return body.trim();

  return `HTTP ${response.status} ${response.statusText}`;
}

// Node fetch has no default timeout, so a wedged daemon would hang the
// agent's tool call forever. Agent dispatches are interactive; admin
// dispatches include long installs (extras can take tens of minutes).
const AGENT_TIMEOUT_MS = 300_000;
const ADMIN_TIMEOUT_MS = 2_000_000;

export function createDaemonClient(baseUrl) {
  const normalized = normalizeBaseUrl(baseUrl);

  async function request(path, payload, defaultTimeoutMs) {
    const timeoutMs =
      Number.isFinite(payload?.timeout_ms) && payload.timeout_ms > 0
        ? payload.timeout_ms
        : defaultTimeoutMs;
    try {
      const res = await fetch(`${normalized}${path}`, {
        method: "POST",
        headers: {
          "content-type": "application/json"
        },
        body: JSON.stringify(payload ?? {}),
        signal: AbortSignal.timeout(timeoutMs)
      });

      const body = await parseJsonOrRaw(res);

      if (!res.ok) {
        return {
          ok: false,
          status: res.status,
          error: responseErrorMessage(res, body),
          response: body
        };
      }

      return body;
    } catch (error) {
      if (error?.name === "TimeoutError" || error?.name === "AbortError") {
        return {
          ok: false,
          error: `Clawdroid daemon did not respond within ${Math.round(timeoutMs / 1000)}s at ${normalized}`,
          path
        };
      }
      const message = error?.message ? `${error.name || "Error"}: ${error.message}` : String(error);
      return {
        ok: false,
        error: `Clawdroid daemon is unavailable at ${normalized}: ${message}`,
        path
      };
    }
  }

  return {
    dispatchAgent(payload) {
      return request("/v1/agent/dispatch", payload, AGENT_TIMEOUT_MS);
    },
    dispatchAdmin(payload) {
      return request("/v1/admin/dispatch", payload, ADMIN_TIMEOUT_MS);
    }
  };
}

function quote(value) {
  const text = String(value ?? "").trim();
  return JSON.stringify(text);
}

/**
 * Returns a confidence emoji indicator based on the node's metadata.
 * Best-effort from whatever fields are available client-side.
 */
function confIndicator(ref, fgPkg) {
  if (typeof ref?.confidence_score === "number") {
    if (ref.confidence_score >= 0.85) return "✓";
    if (ref.confidence_score >= 0.5) return "~";
    return "⚠";
  }
  // Prefer window_rank: lower is better (0=active, 50=other).
  const wr = ref?.window_rank ?? 50;
  const hasLabel = !!(ref?.text || ref?.content_desc || ref?.hint_text);
  const hasActions = Array.isArray(ref?.actions) && ref.actions.length > 0;
  const pkg = ref?.package || "";
  const isForeground = !!(pkg && fgPkg && pkg === fgPkg);
  const isNoise = !!(pkg && pkg !== fgPkg && KNOWN_NOISE_PACKAGES.some(p => String(pkg).startsWith(p)));

  let conf;
  if (isForeground && wr <= 10 && hasActions) conf = "high";
  else if (isForeground && wr <= 20 && (hasLabel || hasActions)) conf = "medium";
  else if (wr <= 10 && hasLabel && hasActions && !isNoise) conf = "high";
  else if (wr <= 20 && hasLabel && !isNoise) conf = "medium";
  else if (wr > 30 || isNoise) conf = "low";
  else conf = "medium";

  return conf === "high" ? "✓" : conf === "medium" ? "~" : "⚠";
}

const KNOWN_NOISE_PACKAGES = [
  "com.android.launcher",
  "com.android.launcher2",
  "com.android.launcher3",
  "com.android.systemui",
  "com.google.android.apps.nexuslauncher",
  "com.google.android.launcher",
  "org.lineageos.jelly",
  "com.miui.home",
  "com.huawei.android.launcher",
  "com.samsung.android.launcher",
];

function isNoisePackage(pkg) {
  return pkg && KNOWN_NOISE_PACKAGES.some(n => String(pkg).startsWith(n));
}

function formatRef(ref, fgPkg) {
  const role = ref?.role || "node";
  const label =
    ref?.semantic_label ||
    ref?.text ||
    ref?.content_desc ||
    ref?.hint_text ||
    ref?.resource_id?.split("/")?.pop() ||
    ref?.class_name?.split(".")?.pop() ||
    ref?.ref;
  const pkg = ref?.package;
  const noise =
    typeof ref?.is_contextual_noise === "boolean"
      ? ref.is_contextual_noise
      : (isNoisePackage(pkg) && pkg !== fgPkg);
  const isForeground = !!(pkg && fgPkg && pkg === fgPkg);
  const detailLabels = [...(ref?.context_labels || []), ...(ref?.secondary_labels || [])]
    .map(value => String(value || "").trim())
    .filter(Boolean)
    .filter((value, index, values) => values.findIndex(other => other.toLowerCase() === value.toLowerCase()) === index);

  const parts = [`${ref?.ref || "?"}`, `[${role}]`, quote(label || "")];
  if (Array.isArray(ref?.actions) && ref.actions.length) {
    parts.push(`actions=${ref.actions.join(",")}`);
  }
  if (ref?.resource_id) {
    parts.push(`id=${ref.resource_id}`);
  }
  if (ref?.content_desc && ref.content_desc !== label) {
    parts.push(`desc=${quote(ref.content_desc)}`);
  }
  if (ref?.hint_text && ref.hint_text !== label) {
    parts.push(`hint=${quote(ref.hint_text)}`);
  }
  if (ref?.checked) parts.push("checked=true");
  if (ref?.selected) parts.push("selected=true");
  if (ref?.focused) parts.push("focused=true");
  if (ref?.label_source && !["text", "content_desc", "hint_text"].includes(ref.label_source)) {
    parts.push(`via=${ref.label_source}`);
  }
  if (isForeground) parts.push("FG");
  else if (pkg) parts.push(`pkg=${pkg}`);
  if (ref?.window_type) parts.push(`win=${ref.window_type}`);
  if (ref?.window_rank != null && ref.window_rank < 50) {
    parts.push(`rank=${ref.window_rank}`);
  }
  if (ref?.parent_ref) {
    parts.push(`parent=${ref.parent_ref}`);
  }
  if (ref?.is_direct_control) parts.push("CONTROL");
  else if (ref?.is_container) parts.push("CONTAINER");
  if (Array.isArray(ref?.child_refs) && ref.child_refs.length) {
    parts.push(`children=${ref.child_refs.length}`);
  }
  if (ref?.container_role) {
    const labelText = ref?.container_label ? `:${quote(ref.container_label)}` : "";
    parts.push(`in=${ref.container_role}${labelText}`);
  } else if (ref?.section_label) {
    parts.push(`section=${quote(ref.section_label)}`);
  }
  if (Array.isArray(ref?.path_labels) && ref.path_labels.length) {
    parts.push(`path=${quote(ref.path_labels.join(" / "))}`);
  }
  if (detailLabels.length) {
    parts.push(`details=${quote(detailLabels.slice(0, 4).join(" | "))}`);
  }
  if (noise) parts.push("NOISE");
  parts.push(confIndicator(ref, fgPkg));
  return parts.join(" ");
}

function summarizeSnapshot(result) {
  const lines = [];
  const fgPkg = result?.foreground_package || result?.package || "";
  const screen = result?.screen_context || null;

  // Header with context
  lines.push(`## Snapshot  ${result.snapshot_id || "?"}`);
  lines.push(`package=${result.package || ""}  activity=${result.activity || ""}`);
  lines.push(`foreground=${fgPkg || "(unknown)"}  mode=${result.mode || "interactive"}  source=${result.source || ""}`);
  const stats = result?.stats || {};
  lines.push(
    `refs=${stats.refs ?? (Array.isArray(result.refs) ? result.refs.length : 0)}  ` +
    `windows=${stats.windows_total ?? 0}  ` +
    `event_seq=${stats.event_seq ?? 0}` +
    (stats.screenshot_auto ? "  [AUTO-SCREENSHOT]" : "") +
    (stats.screenshot_recommended ? "  [SCREENSHOT-RECOMMENDED]" : "")
  );
  if (screen) {
    const contextParts = [`kind=${screen.kind || "screen"}`];
    if (screen.archetype) contextParts.push(`archetype=${quote(screen.archetype)}`);
    if (screen.label) contextParts.push(`label=${quote(screen.label)}`);
    if (screen.best_target_label) contextParts.push(`best_target=${quote(screen.best_target_label)}`);
    if (screen.primary_action_ref || screen.primary_action_label) {
      contextParts.push(`primary_action=${quote(`${screen.primary_action_ref || "?"} ${screen.primary_action_label || ""}`.trim())}`);
    }
    if (screen.dominant_container_label || screen.dominant_container_role) {
      contextParts.push(`container=${quote([screen.dominant_container_role, screen.dominant_container_label].filter(Boolean).join(": "))}`);
    }
    if (screen.path) contextParts.push(`path=${quote(screen.path)}`);
    lines.push(`screen_context: ${contextParts.join("  ")}`);
  }
  const rootDebug = result?.root_debug || null;
  if (rootDebug) {
    const debugParts = [
      `attempts=${rootDebug.attempts ?? 0}`,
      `windows_observed=${rootDebug.windows_observed ?? 0}`,
      `windows_with_root=${rootDebug.windows_with_root ?? 0}`
    ];
    if (rootDebug.empty_reason) debugParts.push(`empty_reason=${quote(rootDebug.empty_reason)}`);
    if (rootDebug.used_focus_fallback) debugParts.push("focus_fallback=true");
    lines.push(`root_debug: ${debugParts.join("  ")}`);
  }

  // Top refs are the best deduped actionable targets for the current screen.
  const topRefs = result?.top_refs?.slice(0, 8) || [];
  if (topRefs.length) {
    lines.push("");
    lines.push("### Best targets:");
    for (const ref of topRefs) {
      lines.push("  " + formatRef(ref, fgPkg));
    }
  }

  // Warnings
  if (Array.isArray(result.warnings) && result.warnings.length) {
    lines.push("");
    for (const warning of result.warnings) {
      lines.push(`⚠ ${warning}`);
    }
  }

  // Screenshot
  if (result?.screenshot_path) {
    lines.push(`screenshot=${result.screenshot_path}`);
  }

  // All refs (limited to 250 for context size).
  const allRefs = result?.refs || [];
  lines.push("");
  lines.push(`### All refs (${allRefs.length}):`);
  for (const ref of allRefs.slice(0, 250)) {
    lines.push(formatRef(ref, fgPkg));
  }
  if (allRefs.length > 250) {
    lines.push(`... ${allRefs.length - 250} additional refs`);
  }

  return lines.join("\n");
}

function summarizeActResult(result) {
  const lines = [];
  lines.push(`## Action  ${result.op || "?"}  ref=${result.ref || "?"}`);
  lines.push(
    `used=${result.used || ""}  verified=${result.verified ? "true" : "false"}  ` +
    `snapshot_stale=${result.snapshot_stale ? "true" : "false"}`
  );
  if (result?.current_app) {
    lines.push(`current_app=${result.current_app.package || ""}  activity=${result.current_app.activity || ""}`);
  }
  if (result?.retry_used) {
    lines.push(`retry_used=${result.retry_used}`);
  }
  if (result?.stale_recovery) {
    const recovery = result.stale_recovery;
    lines.push(
      `stale_recovery: snapshot=${recovery.requested_snapshot_id || ""} -> ${recovery.current_snapshot_id || ""}  ` +
      `ref=${recovery.requested_ref || ""} -> ${recovery.recovered_ref || ""}  ` +
      `label=${quote(recovery.requested_label || "")} -> ${quote(recovery.recovered_label || "")}`
    );
  }
  if (result?.action_resolution) {
    const resolution = result.action_resolution;
    lines.push(
      `action_resolution: ref=${resolution.requested_ref || ""} -> ${resolution.resolved_ref || ""}  ` +
      `label=${quote(resolution.requested_label || "")} -> ${quote(resolution.resolved_label || "")}`
    );
  }

  const verification = result?.verification || null;
  if (verification) {
    const summary = [];
    if (Array.isArray(verification.reasons) && verification.reasons.length) {
      summary.push(`reasons=${verification.reasons.join(",")}`);
    }
    if (verification.matched_post_ref) {
      summary.push(`matched_post_ref=${verification.matched_post_ref}`);
    }
    if (verification.matched_post_label) {
      summary.push(`matched_post_label=${quote(verification.matched_post_label)}`);
    }
    if (typeof verification.settled === "boolean") {
      summary.push(`settled=${verification.settled}`);
    }
    if (summary.length) {
      lines.push(`verification: ${summary.join("  ")}`);
    }

    const before = verification.before || {};
    const after = verification.after || {};
    const transition = [];
    if (before.package || after.package) {
      transition.push(`package=${before.package || ""} -> ${after.package || ""}`);
    }
    if (before.activity || after.activity) {
      transition.push(`activity=${before.activity || ""} -> ${after.activity || ""}`);
    }
    if (before.screen?.kind || after.screen?.kind) {
      transition.push(`screen=${before.screen?.kind || ""} -> ${after.screen?.kind || ""}`);
    }
    if (transition.length) {
      lines.push(`transition: ${transition.join("  ")}`);
    }
  }

  const postSnapshot = result?.post_action_snapshot;
  if (postSnapshot?.ok && postSnapshot?.snapshot_id && Array.isArray(postSnapshot?.refs)) {
    lines.push("");
    lines.push("### Post-action snapshot:");
    lines.push(summarizeSnapshot(postSnapshot));
  } else if (result?.next_step) {
    lines.push(`next_step=${result.next_step}`);
  }

  return lines.join("\n");
}

function summarizeDecisionResult(result) {
  const lines = [];
  lines.push(`## Decide Next  ${result.decision_mode_used || result.decision_mode_requested || "?"}`);
  lines.push(
    `source=${result.decision_source || ""}  ` +
    `snapshot=${result.snapshot_id || "?"}  ` +
    `latency_ms=${result.latency_ms ?? 0}`
  );
  if (result?.provider || result?.model) {
    lines.push(`provider=${result.provider || ""}  model=${result.model || ""}`);
  }
  if (result?.current_app) {
    lines.push(`current_app=${result.current_app.package || ""}  activity=${result.current_app.activity || ""}`);
  }
  if (result?.screen_context) {
    const parts = [`kind=${result.screen_context.kind || "screen"}`];
    if (result.screen_context.archetype) parts.push(`archetype=${quote(result.screen_context.archetype)}`);
    if (result.screen_context.label) parts.push(`label=${quote(result.screen_context.label)}`);
    if (result.screen_context.best_target_label) parts.push(`best_target=${quote(result.screen_context.best_target_label)}`);
    if (result.screen_context.primary_action_ref || result.screen_context.primary_action_label) {
      parts.push(`primary_action=${quote(`${result.screen_context.primary_action_ref || "?"} ${result.screen_context.primary_action_label || ""}`.trim())}`);
    }
    lines.push(`screen_context: ${parts.join("  ")}`);
  }
  if (result?.decision) {
    const decision = result.decision;
    lines.push(
      `decision=${decision.decision || ""}  ref=${decision.ref || ""}  ` +
      `label=${quote(decision.label || "")}  confidence=${Number(decision.confidence || 0).toFixed(2)}`
    );
    if (decision.reason) {
      lines.push(`reason=${quote(decision.reason)}`);
    }
  }
  const refs = result?.top_refs?.slice(0, 6) || [];
  if (refs.length) {
    lines.push("");
    lines.push("### Best targets:");
    const fgPkg = result?.current_app?.package || "";
    for (const ref of refs) {
      lines.push("  " + formatRef(ref, fgPkg));
    }
  }
  if (Array.isArray(result?.warnings) && result.warnings.length) {
    lines.push("");
    for (const warning of result.warnings) {
      lines.push(`⚠ ${warning}`);
    }
  }
  if (result?.execution?.ok) {
    lines.push("");
    lines.push("### Execution:");
    lines.push(summarizeActResult(result.execution));
  }
  return lines.join("\n");
}

function summarizeStoreSearchResult(result) {
  const lines = [];
  lines.push(`## Store Search  ${result.store || "store"}`);
  lines.push(`query=${quote(result.query || "")}`);
  const items = result?.results || [];
  if (!items.length) {
    lines.push("no_results=true");
    return lines.join("\n");
  }
  lines.push("");
  lines.push(`### Candidates (${items.length}):`);
  for (const item of items.slice(0, 10)) {
    const parts = [
      `${item.package || ""}`,
      quote(item.name || ""),
    ];
    if (item.store_name) parts.push(`store=${quote(item.store_name)}`);
    if (item.malware_rank) parts.push(`rank=${item.malware_rank}`);
    if (item.exact_name) parts.push("exact_name=true");
    if (item.exact_package) parts.push("exact_package=true");
    if (item.score != null) parts.push(`score=${item.score}`);
    lines.push(parts.join("  "));
  }
  return lines.join("\n");
}

function summarizeStoreInstallResult(result) {
  const lines = [];
  lines.push(`## Store Install  ${result.store || "store"}`);
  lines.push(`package=${result.package || ""}  ok=${result.ok ? "true" : "false"}`);
  if (result.query) lines.push(`query=${quote(result.query)}`);
  if (result.selection_reason) lines.push(`selection_reason=${result.selection_reason}`);
  const artifact = result?.artifact || {};
  if (artifact.download_url) {
    lines.push(`artifact=${quote(artifact.name || artifact.package || "")}  rank=${artifact.malware_rank || ""}`);
    lines.push(`download_url=${artifact.download_url}`);
  }
  const download = result?.download || {};
  if (download.path) {
    lines.push(`download_path=${download.path}  cached=${download.cached ? "true" : "false"}`);
  }
  const install = result?.install || {};
  if (install.command) {
    lines.push(`install_backend=adb`);
  }
  const verification = result?.verification || {};
  if (verification.package || result.package) {
    lines.push(`installed=${verification.installed ? "true" : "false"}  verified_package=${verification.package || result.package || ""}`);
  }
  if (Array.isArray(result?.candidates) && result.candidates.length) {
    lines.push("");
    lines.push("### Candidates:");
    for (const item of result.candidates.slice(0, 5)) {
      lines.push(`${item.package || ""}  ${quote(item.name || "")}  score=${item.score ?? 0}`);
    }
  }
  if (!result.ok && result?.error) {
    lines.push("");
    lines.push(`error=${quote(result.error)}`);
  }
  return lines.join("\n");
}

function summarizeRouteResult(result) {
  const lines = [];
  const goal = result?.goal || result?.query || "";
  lines.push(`## Route  ${quote(goal)}`);
  lines.push(
    `preferred_backend=${result?.preferred_backend || "desktop_web"}  ` +
    `can_route_to_android=${result?.can_route_to_android ? "true" : "false"}`
  );
  if (result?.service) {
    lines.push(`selected_service=${result.service}`);
  }
  if (result?.reason) {
    lines.push(`reason=${quote(result.reason)}`);
  }
  const selected = result?.selected_match || null;
  if (selected) {
    const parts = [
      `service=${selected.service || ""}`,
      `match_score=${selected.match_score ?? 0}`,
      `matched=${quote(selected.matched_term || "")}`,
      `backend=${selected.preferred_backend || ""}`
    ];
    if (selected.native_package) parts.push(`native_package=${selected.native_package}`);
    if (selected.browser_url) parts.push(`browser_url=${selected.browser_url}`);
    lines.push(`selected: ${parts.join("  ")}`);
    if (selected.recommended_action) {
      lines.push(`recommended_action=${JSON.stringify(selected.recommended_action)}`);
    }
    if (selected.install_option?.available) {
      lines.push(
        `install_option=${selected.install_option.package || ""}  ` +
        `rank=${selected.install_option.malware_rank || ""}  ` +
        `approval=${selected.install_option.requires_approval ? "required" : "not_required"}`
      );
    }
  }
  const matches = result?.matches || [];
  if (matches.length) {
    lines.push("");
    lines.push(`### Matches (${matches.length}):`);
    for (const item of matches.slice(0, 8)) {
      const parts = [
        `${item.service || ""}`,
        `score=${item.match_score ?? 0}`,
        `matched=${quote(item.matched_term || "")}`,
        `backend=${item.preferred_backend || ""}`
      ];
      if (item.native_package) parts.push(`native=${item.native_package}`);
      if (item.browser_url) parts.push(`web=${item.browser_url}`);
      if (item.install_option?.available) parts.push(`install=${item.install_option.package || ""}`);
      lines.push(parts.join("  "));
    }
  }
  return lines.join("\n");
}

export async function summarizeResult(result) {
  let text;
  if (result?.ok && result?.snapshot_id && Array.isArray(result?.refs)) {
    text = summarizeSnapshot(result);
  } else if (result?.ok && (result?.action === "task_route" || result?.action === "service_resolve")) {
    text = summarizeRouteResult(result);
  } else if (result?.action === "store_search" && Array.isArray(result?.results)) {
    text = summarizeStoreSearchResult(result);
  } else if (result?.action === "store_install" && (result?.artifact || result?.verification || result?.download)) {
    text = summarizeStoreInstallResult(result);
  } else if (result?.ok && result?.action === "decide_next" && result?.decision) {
    text = summarizeDecisionResult(result);
  } else if (result?.ok && result?.used) {
    text = summarizeActResult(result);
  } else if (result?.ok && result?.path) {
    // Screenshot-only result.
    text = JSON.stringify(result, null, 2);
  } else {
    text = JSON.stringify(result, null, 2);
  }

  const imagePath =
    (typeof result?.screenshot_path === "string" && result.screenshot_path) ||
    (typeof result?.post_action_snapshot?.screenshot_path === "string" && result.post_action_snapshot.screenshot_path) ||
    (result?.ok && typeof result?.path === "string" && result.path) ||
    null;

  if (imagePath) {
    try {
      const { imageResultFromFile } = await import("openclaw/plugin-sdk/agent-runtime");
      return await imageResultFromFile({
        label: "android-screenshot",
        path: imagePath,
        extraText: text,
        details: {
          snapshot_id: result?.snapshot_id,
          package: result?.package,
          activity: result?.activity,
          foreground_package: result?.foreground_package,
          source: result?.source,
          top_refs:
            result?.top_refs?.map(r => r.ref) ||
            result?.post_action_snapshot?.top_refs?.map(r => r.ref) ||
            []
        }
      });
    } catch {
      return {
        content: [
          {
            type: "text",
            text
          }
        ]
      };
    }
  }

  return {
    content: [
      {
        type: "text",
        text
      }
    ]
  };
}

import { definePluginEntry } from "openclaw/plugin-sdk/core";
import { createDaemonClient, summarizeResult } from "./src/daemon-client.js";
import { AndroidAdminParameters, AndroidParameters } from "./src/schemas.js";

export default definePluginEntry({
  id: "android-waydroid",
  name: "Android (Waydroid)",
  register(api) {
    const cfg = api?.config ?? {};
    const daemonBaseUrl = cfg.daemonBaseUrl || "http://127.0.0.1:48765";
    const allowHostControl = cfg.allowHostControl !== false;
    const client = createDaemonClient(daemonBaseUrl);

    api.registerTool(
      {
        name: "android",
        description:
          "Control Android apps through Waydroid using accessibility-first snapshots and ephemeral refs. Use task_route for branded service requests like Amazon, Uber, DoorDash, Instacart, Airbnb, Reddit, or Spotify when you want to prefer native Android or Android web over desktop web. Use snapshot plus act as the primary interaction loop; use store_search to resolve direct Aptoide package candidates when you want to bypass fragile store install UI.",
        parameters: AndroidParameters,
        async execute(_id, params) {
          const payload = { ...params, tool: "android" };
          if (payload.action === "decide_next") {
            if (!payload.decision_mode && cfg.defaultDecisionMode) {
              payload.decision_mode = cfg.defaultDecisionMode;
            }
            if (!payload.llm_provider && cfg.defaultLlmProvider) {
              payload.llm_provider = cfg.defaultLlmProvider;
            }
            if (!payload.llm_model && cfg.defaultLlmModel) {
              payload.llm_model = cfg.defaultLlmModel;
            }
          }
          const response = await client.dispatchAgent(payload);
          return await summarizeResult(response);
        }
      },
      { optional: true }
    );

    if (!allowHostControl) {
      return;
    }

    api.registerTool(
      {
        name: "android_admin",
        description:
          "Admin and host-control actions for the Android Waydroid stack, including starting Waydroid, direct APK/store installs over ADB, and configuring bridge allowlists.",
        parameters: AndroidAdminParameters,
        async execute(_id, params) {
          const response = await client.dispatchAdmin({ ...params, tool: "android_admin" });
          return await summarizeResult(response);
        }
      },
      { optional: true }
    );
  }
});

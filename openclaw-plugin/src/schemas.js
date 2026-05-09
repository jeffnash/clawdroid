import { Type } from "@sinclair/typebox";

const literalUnion = values => Type.Union(values.map(value => Type.Literal(value)));

const ANDROID_ACTIONS = [
  "status",
  "current_app",
  "apps_list",
  "apps_search",
  "service_resolve",
  "task_route",
  "app_installed",
  "store_search",
  "app_open",
  "activity_start",
  "intent_start",
  "url_open",
  "settings_open",
  "app_details_open",
  "market_open",
  "snapshot",
  "screenshot",
  "decide_next",
  "act",
  "coordinate_act",
  "wait"
];

const ANDROID_ADMIN_ACTIONS = [
  "doctor",
  "recover",
  "waydroid_start",
  "waydroid_stop",
  "app_install",
  "app_install_url",
  "store_install",
  "app_remove",
  "default_stores_install",
  "device_profile_apply",
  "extras_install",
  "extras_uninstall",
  "bridge_configure"
];

const SNAPSHOT_MODES = ["interactive", "full", "hybrid"];
const DECISION_MODES = ["deterministic", "auto", "llm_text", "llm_vision"];
const REF_OPS = [
  "click",
  "click_center",
  "long_click",
  "set_text",
  "clear_text",
  "scroll_forward",
  "scroll_backward",
  "scroll_to_start",
  "scroll_to_end",
  "press_back",
  "press_home",
  "press_recents",
  "press_enter",
  "tap",
  "long_press",
  "swipe",
  "type_text"
];
const WAIT_TARGETS = ["idle", "package", "activity", "text", "ref_appears", "ref_gone"];

export const AndroidParameters = Type.Object({
  action: literalUnion(ANDROID_ACTIONS),
  device: Type.Optional(Type.String()),
  store: Type.Optional(Type.String()),
  package: Type.Optional(Type.String()),
  activity: Type.Optional(Type.String()),
  query: Type.Optional(Type.String()),
  url: Type.Optional(Type.String()),
  intent_action: Type.Optional(Type.String()),
  data_url: Type.Optional(Type.String()),
  mime_type: Type.Optional(Type.String()),
  categories: Type.Optional(Type.Array(Type.String())),
  extras: Type.Optional(Type.Record(Type.String(), Type.String())),
  settings_action: Type.Optional(Type.String()),
  stop: Type.Optional(Type.Boolean()),
  snapshot_mode: Type.Optional(literalUnion(SNAPSHOT_MODES)),
  include_screenshot: Type.Optional(Type.Boolean()),
  goal: Type.Optional(Type.String()),
  decision_mode: Type.Optional(literalUnion(DECISION_MODES)),
  auto_execute: Type.Optional(Type.Boolean()),
  llm_provider: Type.Optional(Type.String()),
  llm_model: Type.Optional(Type.String()),
  snapshot_id: Type.Optional(Type.String()),
  ref: Type.Optional(Type.String()),
  op: Type.Optional(literalUnion(REF_OPS)),
  text: Type.Optional(Type.String()),
  x: Type.Optional(Type.Integer()),
  y: Type.Optional(Type.Integer()),
  x1: Type.Optional(Type.Integer()),
  y1: Type.Optional(Type.Integer()),
  x2: Type.Optional(Type.Integer()),
  y2: Type.Optional(Type.Integer()),
  duration_ms: Type.Optional(Type.Integer({ minimum: 0 })),
  approved: Type.Optional(Type.Boolean()),
  limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 25 })),
  timeout_ms: Type.Optional(Type.Integer({ minimum: 0 })),
  wait_for: Type.Optional(literalUnion(WAIT_TARGETS)),
  wait_value: Type.Optional(Type.String())
});

export const AndroidAdminParameters = Type.Object({
  action: literalUnion(ANDROID_ADMIN_ACTIONS),
  mode: Type.Optional(literalUnion(["user", "system"])),
  store: Type.Optional(Type.String()),
  package: Type.Optional(Type.String()),
  query: Type.Optional(Type.String()),
  apk_path: Type.Optional(Type.String()),
  apk_url: Type.Optional(Type.String()),
  extras: Type.Optional(Type.Array(Type.String())),
  stores: Type.Optional(Type.Array(Type.String())),
  allowed_packages: Type.Optional(Type.Array(Type.String())),
  approved: Type.Optional(Type.Boolean()),
  limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 25 })),
  timeout_ms: Type.Optional(Type.Integer({ minimum: 0 }))
});

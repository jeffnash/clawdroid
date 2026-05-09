package ai.openclaw.androidbridge

import android.util.Base64
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketException
import java.net.URI
import java.net.URLDecoder
import java.security.MessageDigest
import java.security.SecureRandom
import java.nio.charset.StandardCharsets
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

class BridgeHttpServer(
  private val service: OpenClawAccessibilityService,
  private val port: Int = 49317,
) {
  private val executor = Executors.newCachedThreadPool()
  private val started = AtomicBoolean(false)
  private val stopRequested = AtomicBoolean(false)

  @Volatile
  private var serverSocket: ServerSocket? = null

  fun start() {
    if (!started.compareAndSet(false, true)) return
    stopRequested.set(false)
    executor.execute {
      while (!stopRequested.get()) {
        var socket: ServerSocket? = null
        try {
          socket = ServerSocket(port, 50, InetAddress.getByName("127.0.0.1"))
          serverSocket = socket
          BridgeState.updateBridgeListening(true)
          Log.i(TAG, "Bridge HTTP server listening on 127.0.0.1:$port")
          while (!socket.isClosed && !stopRequested.get()) {
            try {
              val client = socket.accept()
              executor.execute { handleClient(client) }
            } catch (t: Throwable) {
              if (!stopRequested.get()) {
                Log.e(TAG, "Socket accept failed", t)
              }
            }
          }
        } catch (t: Throwable) {
          if (stopRequested.get() || t is SocketException) {
            Log.w(TAG, "Bridge HTTP server closed", t)
          } else {
            Log.e(TAG, "Bridge server failed", t)
          }
        } finally {
          BridgeState.updateBridgeListening(false)
          serverSocket = null
          try { socket?.close() } catch (_: Throwable) { /* ignore */ }
        }
        if (!stopRequested.get()) {
          Thread.sleep(RESTART_DELAY_MS)
        }
      }
      started.set(false)
    }
  }

  fun stop() {
    stopRequested.set(true)
    BridgeState.updateBridgeListening(false)
    try { serverSocket?.close() } catch (_: Throwable) { /* ignore */ }
    started.set(false)
  }

  /**
   * Handle a single client connection.
   *
   * The outer try/catch ensures that any exception (including from the
   * client.use {} block) is caught here so the executor thread stays alive
   * and the server continues running.
   */
  private fun handleClient(client: Socket) {
    try {
      client.soTimeout = SOCKET_TIMEOUT_MS
      client.use { socket ->
        val reader = BufferedReader(InputStreamReader(socket.getInputStream()))
        val writer = OutputStreamWriter(socket.getOutputStream())
        try {
          val requestLine = reader.readLine() ?: return@use
          val parts = requestLine.split(" ")
          if (parts.size < 2) {
            Log.w(TAG, "Malformed request line: $requestLine")
            writeJson(writer, 400, jsonError("Malformed request line"))
            return@use
          }
          val method = parts[0]
          val requestUri = URI(parts[1])
          val path = requestUri.path ?: parts[1]
          val query = parseQuery(requestUri.rawQuery)
          Log.i(TAG, "Handling bridge request: $method ${parts[1]}")
          var contentLength = 0
          val headers = mutableMapOf<String, String>()
          while (true) {
            val line = reader.readLine() ?: break
            if (line.isBlank()) break
            val idx = line.indexOf(":")
            if (idx > 0) {
              val name = line.substring(0, idx).trim().lowercase()
              val value = line.substring(idx + 1).trim()
              headers[name] = value
              if (name == "content-length") {
                contentLength = value.toIntOrNull() ?: 0
              }
            }
          }
          val body = if (contentLength > 0) {
            val buffer = CharArray(contentLength)
            var read = 0
            while (read < contentLength) {
              val n = reader.read(buffer, read, contentLength - read)
              if (n <= 0) break
              read += n
            }
            String(buffer, 0, read)
          } else {
            ""
          }
          val payload = if (body.isNotBlank()) JSONObject(body) else JSONObject()
          if (requiresAuth(path) && !isAuthorized(headers)) {
            writeJson(writer, 401, jsonError("Unauthorized bridge request"))
            return@use
          }
          val response = route(method, path, query, payload)
          writeJson(writer, 200, response)
        } catch (t: Throwable) {
          Log.e(TAG, "Bridge route failed", t)
          try {
            writeJson(writer, 500, jsonError(t.message ?: "Internal bridge error"))
          } catch (_: Throwable) {
            // Socket already closed; ignore.
          }
        }
      }
    } catch (t: Throwable) {
      // Socket-level errors (remote close, timeout, reset) are normal when clients
      // disconnect. Only log at debug level to avoid noise.
      val msg = t.message ?: ""
      if (t is SocketException && (msg.contains("closed") || msg.contains("reset"))) {
        Log.d(TAG, "Client disconnected: $msg")
      } else if (t is java.net.SocketTimeoutException) {
        Log.d(TAG, "Client socket timeout")
      } else {
        Log.e(TAG, "Unexpected client error", t)
      }
    }
  }

  private fun requiresAuth(path: String): Boolean {
    return path in setOf("/tree", "/configure", "/node_action", "/global_action")
  }

  private fun isAuthorized(headers: Map<String, String>): Boolean {
    val token = bridgeToken()
    val supplied = headers["x-openclaw-bridge-token"]
      ?: headers["authorization"]?.removePrefix("Bearer ")?.trim()
      ?: ""
    if (supplied.isBlank()) return false
    return MessageDigest.isEqual(
      supplied.toByteArray(StandardCharsets.UTF_8),
      token.toByteArray(StandardCharsets.UTF_8),
    )
  }

  private fun bridgeToken(): String {
    val tokenFile = File(service.filesDir, TOKEN_FILE_NAME)
    val existing = try {
      tokenFile.readText(Charsets.UTF_8).trim()
    } catch (_: Throwable) {
      ""
    }
    if (existing.length >= 32) return existing

    val bytes = ByteArray(32)
    SecureRandom().nextBytes(bytes)
    val generated = Base64.encodeToString(bytes, Base64.NO_WRAP)
    try {
      tokenFile.parentFile?.mkdirs()
      tokenFile.writeText(generated, Charsets.UTF_8)
      tokenFile.setReadable(false, false)
      tokenFile.setReadable(true, true)
      tokenFile.setWritable(false, false)
      tokenFile.setWritable(true, true)
    } catch (t: Throwable) {
      Log.e(TAG, "Failed to persist bridge auth token", t)
    }
    return generated
  }

  private fun route(method: String, path: String, query: Map<String, String>, body: JSONObject): JSONObject {
    return when {
      method == "GET" && path == "/health" -> JSONObject()
        .put("ok", true)
        .put("port", port)
        .put("auth_required", true)
        .put("token_ready", bridgeToken().isNotBlank())
        .put("event_seq", BridgeState.eventSeq.get())
        .put("last_package", BridgeState.lastPackage)
        .put("allowed_packages", JSONArray(BridgeState.allowedPackages.toList()))

      method == "GET" && path == "/tree" -> {
        val rootsSnapshot = BridgeState.currentRootsSnapshot()
        val rankedRoots = rootsSnapshot.roots
        val mode = query["mode"]?.lowercase() ?: "interactive"

        val nodes = NodeMapper.snapshotNodes(rankedRoots, mode)

        val arr = JSONArray()
        nodes.forEach { node -> arr.put(node.toJson()) }

        // Expose foreground package for agent disambiguation.
        val fgPkg = BridgeState.foregroundPackage ?: BridgeState.lastPackage

        JSONObject()
          .put("ok", true)
          .put("mode", mode)
          .put("nodes", arr)
          .put("foreground_package", fgPkg)
          .put("last_package", BridgeState.lastPackage)
          .put("event_seq", BridgeState.eventSeq.get())
          .put("windows_total", rankedRoots.size)
          .put("root_debug", rootsSnapshot.diagnostics.toJson())
      }

      method == "POST" && path == "/configure" -> {
        val arr = body.optJSONArray("allowed_packages") ?: JSONArray()
        val packages = mutableListOf<String>()
        for (i in 0 until arr.length()) {
          packages.add(arr.optString(i))
        }
        BridgeState.applyAllowedPackages(packages)
        JSONObject()
          .put("ok", true)
          .put("allowed_packages", JSONArray(BridgeState.allowedPackages.toList()))
      }

      method == "POST" && path == "/node_action" -> {
        val nodeKey = body.optString("node_key")
        val action = body.optString("action")
        val text = if (body.has("text")) body.optString("text") else null
        val ok = service.performNodeAction(nodeKey, action, text)
        JSONObject().put("ok", ok).put("node_key", nodeKey).put("action", action)
      }

      method == "POST" && path == "/global_action" -> {
        val action = body.optString("action")
        val ok = service.performGlobal(action)
        JSONObject().put("ok", ok).put("action", action)
      }

      else -> jsonError("Unknown route: $method $path")
    }
  }

  private fun writeJson(writer: OutputStreamWriter, status: Int, payload: JSONObject) {
    val reason = when (status) {
      200 -> "OK"
      400 -> "Bad Request"
      500 -> "Internal Server Error"
      else -> "OK"
    }
    val body = payload.toString()
    writer.write("HTTP/1.1 $status $reason\r\n")
    writer.write("Content-Type: application/json\r\n")
    writer.write("Content-Length: ${body.toByteArray(StandardCharsets.UTF_8).size}\r\n")
    writer.write("Connection: close\r\n")
    writer.write("\r\n")
    writer.write(body)
    writer.flush()
  }

  private fun jsonError(message: String): JSONObject =
    JSONObject().put("ok", false).put("error", message)

  private fun parseQuery(rawQuery: String?): Map<String, String> {
    if (rawQuery.isNullOrBlank()) return emptyMap()
    return rawQuery
      .split("&")
      .mapNotNull { pair ->
        val idx = pair.indexOf("=")
        if (idx < 0) {
          val key = pair.trim()
          if (key.isBlank()) null else key to ""
        } else {
          val key = pair.substring(0, idx).trim()
          if (key.isBlank()) {
            null
          } else {
            key to URLDecoder.decode(pair.substring(idx + 1), StandardCharsets.UTF_8.name())
          }
        }
      }
      .toMap()
  }

  private fun UiNode.toJson(): JSONObject {
    return JSONObject()
      .put("node_key", nodeKey)
      .put("text", text)
      .put("content_desc", contentDesc)
      .put("hint_text", hintText)
      .put("resource_id", resourceId)
      .put("class_name", className)
      .put("bounds", JSONArray(bounds.toList()))
      .put("actions", JSONArray(actions))
      .put("role", role)
      .put("editable", editable)
      .put("scrollable", scrollable)
      .put("enabled", enabled)
      .put("selected", selected)
      .put("checked", checked)
      .put("clickable", clickable)
      .put("long_clickable", longClickable)
      .put("checkable", checkable)
      .put("focusable", focusable)
      .put("focused", focused)
      .put("visible", visible)
      .put("child_count", childCount)
      .put("package", packageName)
      .put("source", "bridge")
      .put("window_rank", windowRank)
      .put("window_type", windowType)
      .put("active_window", activeWindow)
      .put("depth", depth)
      .put("sibling_index", siblingIndex)
      .put("parent_key", parentKey)
      .put("parent_role", parentRole)
      .put("parent_label", parentLabel)
      .put("semantic_id", semanticId)
      .put("semantic_label", semanticLabel)
      .put("label_source", labelSource)
      .put("container_key", containerKey)
      .put("container_role", containerRole)
      .put("container_label", containerLabel)
      .put("section_key", sectionKey)
      .put("section_role", sectionRole)
      .put("section_label", sectionLabel)
      .put("path_labels", JSONArray(pathLabels))
  }

  private fun BridgeState.RootDiagnostics.toJson(): JSONObject {
    return JSONObject()
      .put("attempts", attempts)
      .put("active_root_present", activeRootPresent)
      .put("active_window_id", activeWindowId)
      .put("active_package", activePackage)
      .put("foreground_package", foregroundPackage)
      .put("windows_observed", windowsObserved)
      .put("windows_with_root", windowsWithRoot)
      .put("rootless_window_ids", JSONArray(rootlessWindowIds))
      .put("focus_root_present", focusRootPresent)
      .put("focus_window_id", focusWindowId)
      .put("focus_package", focusPackage)
      .put("used_focus_fallback", usedFocusFallback)
      .put("empty_reason", emptyReason)
  }

  companion object {
    private const val TAG = "OpenClawBridgeHttp"
    /** Delay before restarting after a fatal server error. */
    private const val RESTART_DELAY_MS = 250L
    /** Socket read timeout to avoid hanging on slow/disconnected clients. */
    private const val SOCKET_TIMEOUT_MS = 10_000
    private const val TOKEN_FILE_NAME = "bridge_token"
  }
}

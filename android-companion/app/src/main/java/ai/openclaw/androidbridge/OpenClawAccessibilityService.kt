package ai.openclaw.androidbridge

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityServiceInfo
import android.os.Bundle
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

class OpenClawAccessibilityService : AccessibilityService() {
    private var bridgeServer: BridgeHttpServer? = null

    private fun ensureBridgeServer() {
        val existing = bridgeServer
        if (existing != null) {
            existing.start()
            return
        }
        bridgeServer = BridgeHttpServer(this).also { it.start() }
    }

    override fun onCreate() {
        super.onCreate()
        Log.i(TAG, "Accessibility service created")
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        BridgeState.attachService(this)
        applyAllowedPackages(BridgeState.allowedPackages.toList())
        ensureBridgeServer()
        Log.i(TAG, "Accessibility service connected")
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        event ?: return
        BridgeState.updateEvent(event.packageName?.toString(), event.eventType)
        ensureBridgeServer()
    }

    override fun onInterrupt() {
        Log.w(TAG, "Accessibility service interrupted")
    }

    override fun onDestroy() {
        bridgeServer?.stop()
        bridgeServer = null
        BridgeState.detachService(this)
        Log.i(TAG, "Accessibility service destroyed")
        super.onDestroy()
    }

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        bridgeServer?.stop()
        bridgeServer = null
        BridgeState.detachService(this)
        Log.i(TAG, "Accessibility service unbound")
        return super.onUnbind(intent)
    }

    fun applyAllowedPackages(packages: List<String>) {
        val info = serviceInfo ?: AccessibilityServiceInfo()
        info.packageNames = packages.takeIf { it.isNotEmpty() }?.toTypedArray()
        info.eventTypes = AccessibilityEvent.TYPES_ALL_MASK
        info.feedbackType = AccessibilityServiceInfo.FEEDBACK_GENERIC
        info.notificationTimeout = 100
        info.flags = info.flags or AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS or
                AccessibilityServiceInfo.FLAG_INCLUDE_NOT_IMPORTANT_VIEWS or
                AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
        serviceInfo = info
    }

    fun performNodeAction(nodeKey: String, action: String, text: String? = null): Boolean {
        val node = resolveNode(nodeKey) ?: return false
        return when (action) {
            "click" -> node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
            "long_click" -> node.performAction(AccessibilityNodeInfo.ACTION_LONG_CLICK)
            "scroll_forward" -> node.performAction(AccessibilityNodeInfo.ACTION_SCROLL_FORWARD)
            "scroll_backward" -> node.performAction(AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD)
            "set_text" -> {
                val args = Bundle().apply {
                    putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text ?: "")
                }
                node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
            }
            "clear_text" -> {
                val args = Bundle().apply {
                    putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, "")
                }
                node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
            }
            else -> false
        }
    }

    fun performGlobal(action: String): Boolean {
        return when (action) {
            "press_back" -> performGlobalAction(GLOBAL_ACTION_BACK)
            "press_home" -> performGlobalAction(GLOBAL_ACTION_HOME)
            "press_recents" -> performGlobalAction(GLOBAL_ACTION_RECENTS)
            else -> false
        }
    }

    private fun resolveNode(nodeKey: String): AccessibilityNodeInfo? {
        val rawSegments = nodeKey.split(".")
        if (rawSegments.isEmpty()) return null

        val first = rawSegments.first()
        val currentRoot = if (first.startsWith("w")) {
            val windowId = first.removePrefix("w").toIntOrNull() ?: return null
            resolveRootForWindow(windowId)
        } else {
            rootInActiveWindow
        } ?: return null

        val segments = rawSegments.drop(1).mapNotNull { it.toIntOrNull() }
        var current: AccessibilityNodeInfo? = currentRoot
        for ((index, segment) in segments.withIndex()) {
            if (index == 0 && segment == 0) continue
            current = current?.getChild(segment) ?: return null
        }
        return current
    }

    private fun resolveRootForWindow(windowId: Int): AccessibilityNodeInfo? {
        rootInActiveWindow?.let { root ->
            if (root.windowId == windowId) {
                return root
            }
        }
        return try {
            windows?.firstNotNullOfOrNull { window ->
                val root = window.root ?: return@firstNotNullOfOrNull null
                if (root.windowId == windowId) root else null
            }
        } catch (_: Throwable) {
            null
        }
    }

    companion object {
        private const val TAG = "OpenClawAccessibility"
    }
}

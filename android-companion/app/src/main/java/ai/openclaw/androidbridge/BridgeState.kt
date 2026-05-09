package ai.openclaw.androidbridge

import android.os.SystemClock
import android.view.accessibility.AccessibilityWindowInfo
import android.view.accessibility.AccessibilityNodeInfo
import java.util.concurrent.atomic.AtomicLong

object BridgeState {
  @Volatile var service: OpenClawAccessibilityService? = null
  @Volatile var bridgeListening: Boolean = false
  @Volatile var lastPackage: String? = null
  @Volatile var lastEventType: Int? = null
  @Volatile var lastUpdatedAt: Long = 0L
  val eventSeq = AtomicLong(0L)
  @Volatile var allowedPackages: Set<String> = emptySet()

  /** The foreground package as of the last window refresh. Used to rank roots. */
  @Volatile var foregroundPackage: String? = null

  // Known launcher / system UI packages — these are de-prioritized unless they are the
  // actual foreground app (e.g. user is on the home screen).
  private val systemNoisePackages = setOf(
    "com.android.launcher",
    "com.android.launcher3",
    "com.android.systemui",
    "com.google.android.apps.nexuslauncher",
    "com.google.android.launcher",
    "org.lineageos.jelly",
    "com.android.launcher2",
    "com.miui.home",
    "com.huawei.android.launcher",
    "com.samsung.android.launcher",
  )

  fun attachService(service: OpenClawAccessibilityService) {
    this.service = service
  }

  fun detachService(service: OpenClawAccessibilityService) {
    if (this.service === service) {
      this.service = null
    }
    bridgeListening = false
  }

  fun updateBridgeListening(listening: Boolean) {
    bridgeListening = listening
  }

  fun currentRoot(): AccessibilityNodeInfo? = service?.rootInActiveWindow

  /**
   * Returns all accessible windows, ranked by foreground relevance:
   *  1. Active window (foreground app)
   *  2. Other windows from the foreground package
   *  3. Launcher / SystemUI windows (only if active window is also launcher/systemui)
   *  4. All other windows (overlays, assistant, etc.)
   *
   * Each entry carries its windowId and resolved package.
   */
  data class RankedRoot(
    val windowId: Int,
    val root: AccessibilityNodeInfo,
    val packageName: String?,
    val rank: Int,  // lower = more important
    val windowType: String? = null,
    val activeWindow: Boolean = false,
  )

  data class RootDiagnostics(
    val attempts: Int,
    val activeRootPresent: Boolean,
    val activeWindowId: Int?,
    val activePackage: String?,
    val foregroundPackage: String?,
    val windowsObserved: Int,
    val windowsWithRoot: Int,
    val rootlessWindowIds: List<Int>,
    val focusRootPresent: Boolean,
    val focusWindowId: Int?,
    val focusPackage: String?,
    val usedFocusFallback: Boolean,
    val emptyReason: String?,
  )

  data class RankedRootsSnapshot(
    val roots: List<RankedRoot>,
    val diagnostics: RootDiagnostics,
  )

  private data class RootCollectionAttempt(
    val roots: List<RankedRoot>,
    val diagnostics: RootDiagnostics,
  )

  fun currentRootsSnapshot(): RankedRootsSnapshot {
    val svc = service ?: return RankedRootsSnapshot(
      roots = emptyList(),
      diagnostics = RootDiagnostics(
        attempts = 1,
        activeRootPresent = false,
        activeWindowId = null,
        activePackage = null,
        foregroundPackage = foregroundPackage,
        windowsObserved = 0,
        windowsWithRoot = 0,
        rootlessWindowIds = emptyList(),
        focusRootPresent = false,
        focusWindowId = null,
        focusPackage = null,
        usedFocusFallback = false,
        emptyReason = "service-missing",
      ),
    )

    var lastAttempt: RootCollectionAttempt? = null
    repeat(ROOT_COLLECTION_ATTEMPTS) { index ->
      val attempt = collectRootsOnce(svc)
      if (attempt.roots.isNotEmpty()) {
        return RankedRootsSnapshot(
          roots = attempt.roots.sortedBy { it.rank },
          diagnostics = attempt.diagnostics.copy(attempts = index + 1, emptyReason = null),
        )
      }
      lastAttempt = attempt
      if (index + 1 < ROOT_COLLECTION_ATTEMPTS) {
        SystemClock.sleep(ROOT_COLLECTION_RETRY_DELAY_MS)
      }
    }

    val diagnostics = lastAttempt?.diagnostics?.copy(
      attempts = ROOT_COLLECTION_ATTEMPTS,
      emptyReason = lastAttempt?.diagnostics?.emptyReason ?: "no-roots-after-retry",
    ) ?: RootDiagnostics(
      attempts = ROOT_COLLECTION_ATTEMPTS,
      activeRootPresent = false,
      activeWindowId = null,
      activePackage = null,
      foregroundPackage = foregroundPackage,
      windowsObserved = 0,
      windowsWithRoot = 0,
      rootlessWindowIds = emptyList(),
      focusRootPresent = false,
      focusWindowId = null,
      focusPackage = null,
      usedFocusFallback = false,
      emptyReason = "no-roots-after-retry",
    )
    return RankedRootsSnapshot(emptyList(), diagnostics)
  }

  fun currentRootsRanked(): List<RankedRoot> {
    return currentRootsSnapshot().roots
  }

  private fun collectRootsOnce(svc: OpenClawAccessibilityService): RootCollectionAttempt {
    val activeRoot = svc.rootInActiveWindow
    val activePackage = activeRoot?.packageName?.toString()
    val activeWindowId = activeRoot?.windowId

    // Update cached foreground package if we have an active root
    if (activePackage != null) {
      foregroundPackage = activePackage
    }

    val rankedForegroundPackage = activePackage ?: foregroundPackage ?: lastPackage

    val results = mutableListOf<RankedRoot>()
    val seenWindowIds = mutableSetOf<Int>()
    val rootlessWindowIds = mutableListOf<Int>()
    var windowsObserved = 0
    var windowsWithRoot = 0

    // Collect other windows
    try {
      val windows = svc.windows ?: emptyList()
      windowsObserved = windows.size
      windows.forEach { window ->
        try {
          val root = window.root
          if (root == null) {
            rootlessWindowIds.add(window.id)
            return@forEach
          }
          windowsWithRoot += 1
          val pkg = root.packageName?.toString()
          val isActiveWindow = root.windowId == activeWindowId
          seenWindowIds.add(root.windowId)
          results.add(RankedRoot(
            windowId = root.windowId,
            root = root,
            packageName = pkg,
            rank = if (isActiveWindow) 0 else rankFor(pkg, rankedForegroundPackage),
            windowType = windowTypeName(window.type),
            activeWindow = isActiveWindow,
          ))
        } catch (_: Throwable) {
          // window access can throw on some devices
        }
      }
    } catch (_: Throwable) {
      // windows enumeration can be blocked on some builds
    }

    if (activeRoot != null && !seenWindowIds.contains(activeRoot.windowId)) {
      results.add(RankedRoot(
        windowId = activeRoot.windowId,
        root = activeRoot,
        packageName = activeRoot.packageName?.toString(),
        rank = 0,
        windowType = "application",
        activeWindow = true,
      ))
    }

    var usedFocusFallback = false
    var focusRootPresent = false
    var focusWindowId: Int? = null
    var focusPackage: String? = null
    if (results.isEmpty()) {
      val focusRoot = focusFallbackRoot(svc)
      if (focusRoot != null) {
        focusRootPresent = true
        focusWindowId = focusRoot.windowId
        focusPackage = focusRoot.packageName?.toString()
        results.add(RankedRoot(
          windowId = focusRoot.windowId,
          root = focusRoot,
          packageName = focusPackage,
          rank = 0,
          windowType = "focus_fallback",
          activeWindow = true,
        ))
        usedFocusFallback = true
        if (focusPackage != null) {
          foregroundPackage = focusPackage
        }
      }
    }

    val emptyReason = when {
      results.isNotEmpty() -> null
      windowsObserved == 0 && activeRoot == null -> "windows-empty-active-root-null"
      windowsObserved > 0 && windowsWithRoot == 0 -> "windows-present-but-roots-null"
      activeRoot == null -> "active-root-null"
      else -> "unknown"
    }

    return RootCollectionAttempt(
      roots = results.sortedBy { it.rank },
      diagnostics = RootDiagnostics(
        attempts = 1,
        activeRootPresent = activeRoot != null,
        activeWindowId = activeWindowId,
        activePackage = activePackage,
        foregroundPackage = foregroundPackage ?: rankedForegroundPackage,
        windowsObserved = windowsObserved,
        windowsWithRoot = windowsWithRoot,
        rootlessWindowIds = rootlessWindowIds,
        focusRootPresent = focusRootPresent,
        focusWindowId = focusWindowId,
        focusPackage = focusPackage,
        usedFocusFallback = usedFocusFallback,
        emptyReason = emptyReason,
      ),
    )
  }

  /**
   * Rank calculation:
   * 0  = active window (same windowId as rootInActiveWindow)
   * 10 = windows from the foreground package
   * 20 = launcher / systemui windows (only relevant when foreground is also launcher)
   * 30 = launcher / systemui windows when foreground is a real app
   * 40 = other windows (overlays, assistant, etc.)
   */
  private fun rankFor(windowPackage: String?, foregroundPackage: String?): Int {
    if (windowPackage == foregroundPackage) return 10
    val isNoise = systemNoisePackages.any { windowPackage?.startsWith(it) == true }
    if (isNoise) {
      // Launcher/SystemUI is only interesting when the foreground IS the launcher
      return if (systemNoisePackages.any { foregroundPackage?.startsWith(it) == true }) 20 else 30
    }
    return 40
  }

  private fun windowTypeName(type: Int): String {
    return when (type) {
      AccessibilityWindowInfo.TYPE_APPLICATION -> "application"
      AccessibilityWindowInfo.TYPE_INPUT_METHOD -> "input_method"
      AccessibilityWindowInfo.TYPE_SYSTEM -> "system"
      AccessibilityWindowInfo.TYPE_ACCESSIBILITY_OVERLAY -> "accessibility_overlay"
      AccessibilityWindowInfo.TYPE_SPLIT_SCREEN_DIVIDER -> "split_screen_divider"
      else -> "other"
    }
  }

  /**
   * Legacy list-returning API. Returns roots in ranked order.
   */
  fun currentRoots(): List<AccessibilityNodeInfo> {
    return currentRootsRanked().map { it.root }
  }

  private fun focusFallbackRoot(svc: OpenClawAccessibilityService): AccessibilityNodeInfo? {
    val focusCandidates = listOf(
      AccessibilityNodeInfo.FOCUS_INPUT,
      AccessibilityNodeInfo.FOCUS_ACCESSIBILITY,
    )
    focusCandidates.forEach { focusType ->
      val focusedNode = try {
        svc.findFocus(focusType)
      } catch (_: Throwable) {
        null
      }
      val root = topAncestor(focusedNode)
      if (root != null) {
        return root
      }
    }
    return null
  }

  private fun topAncestor(node: AccessibilityNodeInfo?): AccessibilityNodeInfo? {
    var current = node ?: return null
    while (true) {
      val parent = try {
        current.parent
      } catch (_: Throwable) {
        null
      } ?: break
      current = parent
    }
    return current
  }

  fun updateEvent(packageName: String?, eventType: Int) {
    lastPackage = packageName
    lastEventType = eventType
    lastUpdatedAt = System.currentTimeMillis()
    eventSeq.incrementAndGet()
  }

  fun applyAllowedPackages(packages: List<String>) {
    allowedPackages = packages.filter { it.isNotBlank() }.toSet()
    service?.applyAllowedPackages(allowedPackages.toList())
  }

  private const val ROOT_COLLECTION_ATTEMPTS = 5
  private const val ROOT_COLLECTION_RETRY_DELAY_MS = 75L
}

package ai.openclaw.androidbridge

import android.graphics.Rect
import android.view.accessibility.AccessibilityNodeInfo

object NodeMapper {
  private data class StructuralContext(
    val parentKey: String? = null,
    val parentRole: String? = null,
    val parentLabel: String? = null,
    val depth: Int = 0,
    val siblingIndex: Int = 0,
    val containerKey: String? = null,
    val containerRole: String? = null,
    val containerLabel: String? = null,
    val sectionKey: String? = null,
    val sectionRole: String? = null,
    val sectionLabel: String? = null,
    val pathLabels: List<String> = emptyList(),
    val activeWindow: Boolean = false,
    val windowType: String? = null,
  )

  /**
   * Snapshot nodes from multiple windows, each carrying its rank (lower = more foreground).
   */
  fun snapshotNodes(rankedRoots: List<BridgeState.RankedRoot>, mode: String): List<UiNode> {
    val nodes = mutableListOf<UiNode>()
    val normalizedMode = normalizeMode(mode)
    for (rankedRoot in rankedRoots) {
      val context = StructuralContext(
        activeWindow = rankedRoot.activeWindow,
        windowType = rankedRoot.windowType,
      )
      walk(
        node = rankedRoot.root,
        path = "w${rankedRoot.root.windowId}.0",
        mode = normalizedMode,
        acc = nodes,
        windowRank = rankedRoot.rank,
        context = context,
      )
    }
    return nodes
  }

  /**
   * Single-root overload: wraps the root with default rank 50.
   */
  fun snapshotNodes(root: AccessibilityNodeInfo?, mode: String): List<UiNode> {
    if (root == null) return emptyList()
    val rankedRoot = BridgeState.RankedRoot(
      windowId = root.windowId,
      root = root,
      packageName = root.packageName?.toString(),
      rank = 50,
      windowType = null,
      activeWindow = true,
    )
    return snapshotNodes(listOf(rankedRoot), mode)
  }

  fun interactiveNodes(root: AccessibilityNodeInfo?): List<UiNode> = snapshotNodes(root, "interactive")

  private fun walk(
    node: AccessibilityNodeInfo,
    path: String,
    mode: String,
    acc: MutableList<UiNode>,
    windowRank: Int,
    context: StructuralContext,
  ) {
    val clickable = node.isClickable
    val longClickable = node.isLongClickable
    val scrollable = node.isScrollable
    val editable = node.className?.toString()?.endsWith("EditText") == true || node.isEditableCompat()
    val focusable = node.isFocusable
    val focused = node.isFocused
    val checkable = node.isCheckable
    val checked = node.isChecked
    val selected = node.isSelected
    val enabled = node.isEnabled
    val visible = node.isVisibleCompat()
    val text = node.text?.toString().orEmpty()
    val contentDesc = node.contentDescription?.toString().orEmpty()
    val hintText = node.hintTextCompat()
    val resourceId = node.viewIdResourceName.orEmpty()
    val className = node.className?.toString().orEmpty()
    val childCount = node.childCount
    val rect = Rect()
    node.getBoundsInScreen(rect)
    val role = roleFor(className, editable, scrollable, clickable)
    val ownLabel = refinedSemanticLabelFor(
      node = node,
      text = text,
      contentDesc = contentDesc,
      hintText = hintText,
      resourceId = resourceId,
      className = className,
      role = role,
      actionable = clickable || longClickable,
    )
    val ownContainerRole = containerRoleFor(className, scrollable, childCount)
    val ownSectionRole = sectionRoleFor(className, ownLabel.first, childCount, ownContainerRole)
    val effectiveContainerKey = ownContainerRole?.let { path } ?: context.containerKey
    val effectiveContainerRole = ownContainerRole ?: context.containerRole
    val effectiveContainerLabel = if (ownContainerRole != null && ownLabel.first.isNotBlank()) {
      ownLabel.first
    } else {
      context.containerLabel
    }
    val effectiveSectionKey = if (ownSectionRole != null) path else context.sectionKey
    val effectiveSectionRole = ownSectionRole ?: context.sectionRole
    val effectiveSectionLabel = if (ownSectionRole != null && ownLabel.first.isNotBlank()) {
      ownLabel.first
    } else {
      context.sectionLabel
    }
    val hasSemanticText = text.isNotBlank() || contentDesc.isNotBlank() || hintText.isNotBlank()
    val interactive = clickable || longClickable || scrollable || editable
    val stateful = editable || focusable || focused || checkable || checked || selected
    val include = shouldIncludeNode(
      mode = mode,
      visible = visible && rect.width() > 0 && rect.height() > 0,
      interactive = interactive,
      hasSemanticText = hasSemanticText,
      stateful = stateful,
      resourceId = resourceId,
      childCount = childCount,
    )
    if (include) {
      val actions = mutableListOf<String>()
      if (clickable) actions.add("click")
      if (longClickable) actions.add("long_click")
      if (scrollable) {
        actions.add("scroll_forward")
        actions.add("scroll_backward")
      }
      if (editable) {
        actions.add("set_text")
        actions.add("clear_text")
      }
      acc.add(
        UiNode(
          nodeKey = path,
          text = text,
          contentDesc = contentDesc,
          hintText = hintText,
          resourceId = resourceId,
          className = className,
          bounds = intArrayOf(rect.left, rect.top, rect.right, rect.bottom),
          actions = actions,
          role = role,
          editable = editable,
          scrollable = scrollable,
          enabled = enabled,
          selected = selected,
          checked = checked,
          clickable = clickable,
          longClickable = longClickable,
          checkable = checkable,
          focusable = focusable,
          focused = focused,
          visible = visible,
          childCount = childCount,
          packageName = node.packageName?.toString(),
          windowRank = windowRank,
          windowType = context.windowType,
          activeWindow = context.activeWindow,
          depth = context.depth,
          siblingIndex = context.siblingIndex,
          parentKey = context.parentKey,
          parentRole = context.parentRole,
          parentLabel = context.parentLabel,
          semanticId = semanticIdFor(
            packageName = node.packageName?.toString(),
            className = className,
            resourceId = resourceId,
            semanticLabel = ownLabel.first,
            depth = context.depth,
            siblingIndex = context.siblingIndex,
          ),
          semanticLabel = ownLabel.first,
          labelSource = ownLabel.second,
          containerKey = effectiveContainerKey,
          containerRole = effectiveContainerRole,
          containerLabel = effectiveContainerLabel,
          sectionKey = effectiveSectionKey,
          sectionRole = effectiveSectionRole,
          sectionLabel = effectiveSectionLabel,
          pathLabels = context.pathLabels,
        )
      )
    }
    val childPathLabels = nextPathLabels(context.pathLabels, ownLabel.first, ownSectionRole, ownContainerRole)
    for (i in 0 until node.childCount) {
      val child = node.getChild(i) ?: continue
      walk(
        node = child,
        path = "$path.$i",
        mode = mode,
        acc = acc,
        windowRank = windowRank,
        context = StructuralContext(
          parentKey = path,
          parentRole = role,
          parentLabel = ownLabel.first.takeIf { it.isNotBlank() },
          depth = context.depth + 1,
          siblingIndex = i,
          containerKey = effectiveContainerKey,
          containerRole = effectiveContainerRole,
          containerLabel = effectiveContainerLabel,
          sectionKey = effectiveSectionKey,
          sectionRole = effectiveSectionRole,
          sectionLabel = effectiveSectionLabel,
          pathLabels = childPathLabels,
          activeWindow = context.activeWindow,
          windowType = context.windowType,
        ),
      )
    }
  }

  private fun normalizeMode(mode: String?): String {
    return when (mode?.lowercase()) {
      "interactive", "hybrid", "full" -> mode.lowercase()
      else -> "interactive"
    }
  }

  private fun shouldIncludeNode(
    mode: String,
    visible: Boolean,
    interactive: Boolean,
    hasSemanticText: Boolean,
    stateful: Boolean,
    resourceId: String,
    childCount: Int,
  ): Boolean {
    if (!visible) return false
    return when (mode) {
      "interactive" -> interactive
      "hybrid" -> interactive || hasSemanticText || stateful
      else -> interactive || hasSemanticText || stateful || resourceId.isNotBlank() || childCount > 0
    }
  }

  private fun roleFor(className: String, editable: Boolean, scrollable: Boolean, clickable: Boolean): String {
    val lower = className.lowercase()
    return when {
      editable || lower.contains("edittext") -> "textbox"
      scrollable -> "scrollview"
      lower.contains("button") -> "button"
      lower.contains("checkbox") || lower.contains("switch") -> "checkbox"
      clickable -> "button"
      else -> "text"
    }
  }

  private fun semanticLabelFor(
    text: String,
    contentDesc: String,
    hintText: String,
    resourceId: String,
    className: String,
    role: String,
  ): Pair<String, String> {
    if (text.isNotBlank()) return text.trim() to "text"
    if (contentDesc.isNotBlank()) return contentDesc.trim() to "content_desc"
    if (hintText.isNotBlank()) return hintText.trim() to "hint_text"
    val resourceToken = normalizeToken(resourceId.substringAfterLast('/', ""))
    if (resourceToken.isNotBlank()) return resourceToken to "resource_id"
    val classToken = normalizeToken(className.substringAfterLast('.'))
    if (classToken.isNotBlank()) return classToken to "class_name"
    return role to "role"
  }

  private fun refinedSemanticLabelFor(
    node: AccessibilityNodeInfo,
    text: String,
    contentDesc: String,
    hintText: String,
    resourceId: String,
    className: String,
    role: String,
    actionable: Boolean,
  ): Pair<String, String> {
    val base = semanticLabelFor(text, contentDesc, hintText, resourceId, className, role)
    if (base.second !in setOf("resource_id", "class_name", "role")) {
      return base
    }
    val childLabels = mutableListOf<Pair<String, String>>()
    for (i in 0 until node.childCount) {
      val child = node.getChild(i) ?: continue
      val childText = child.text?.toString().orEmpty().trim()
      val childDesc = child.contentDescription?.toString().orEmpty().trim()
      val childHint = child.hintTextCompat().trim()
      when {
        childText.isNotBlank() -> childLabels.add(childText to "child_text")
        childDesc.isNotBlank() -> childLabels.add(childDesc to "child_content_desc")
        childHint.isNotBlank() -> childLabels.add(childHint to "child_hint_text")
      }
    }
    if (childLabels.isEmpty()) {
      return base
    }
    val unique = childLabels.distinctBy { it.first.lowercase() }
    if (unique.size == 1) {
      return unique.first()
    }
    if (actionable) {
      val joined = unique.joinToString(" ") { it.first }.trim()
      if (joined.isNotBlank()) {
        return joined to "child_text"
      }
    }
    return unique.first()
  }

  private fun semanticIdFor(
    packageName: String?,
    className: String,
    resourceId: String,
    semanticLabel: String,
    depth: Int,
    siblingIndex: Int,
  ): String {
    if (resourceId.isNotBlank()) return resourceId
    val pkg = packageName ?: "unknown"
    val classToken = className.substringAfterLast('.', "node").ifBlank { "node" }
    val labelToken = normalizeToken(semanticLabel).replace(' ', '_')
    return if (labelToken.isNotBlank()) {
      "$pkg:$classToken:$labelToken"
    } else {
      "$pkg:$classToken:d$depth:i$siblingIndex"
    }
  }

  private fun nextPathLabels(
    current: List<String>,
    currentLabel: String,
    sectionRole: String?,
    containerRole: String?,
  ): List<String> {
    if (currentLabel.isBlank()) return current
    val shouldAppend = sectionRole != null || containerRole != null
    if (!shouldAppend) return current
    if (current.lastOrNull() == currentLabel) return current
    return (current + currentLabel).takeLast(5)
  }

  private fun containerRoleFor(className: String, scrollable: Boolean, childCount: Int): String? {
    val lower = className.lowercase()
    return when {
      lower.contains("recyclerview") || lower.contains("listview") -> "list"
      lower.contains("gridview") -> "grid"
      lower.contains("viewpager") || lower.contains("pager") -> "pager"
      lower.contains("tablayout") || lower.contains("tabwidget") -> "tabs"
      lower.contains("toolbar") || lower.contains("appbar") -> "toolbar"
      lower.contains("bottomnavigation") || lower.contains("navigationbar") -> "bottom_nav"
      lower.contains("drawerlayout") -> "drawer"
      lower.contains("dialog") -> "dialog"
      scrollable -> "scrollview"
      childCount > 0 && (lower.contains("layout") || lower.contains("card")) -> "group"
      else -> null
    }
  }

  private fun sectionRoleFor(
    className: String,
    semanticLabel: String,
    childCount: Int,
    containerRole: String?,
  ): String? {
    if (semanticLabel.isBlank() || childCount <= 0) return null
    val lower = className.lowercase()
    return when {
      lower.contains("toolbar") || lower.contains("appbar") -> "toolbar"
      lower.contains("dialog") -> "dialog"
      containerRole == "list" || containerRole == "grid" -> containerRole
      else -> "section"
    }
  }

  private fun normalizeToken(value: String): String {
    if (value.isBlank()) return ""
    return value
      .replace(Regex("([a-z])([A-Z])"), "$1 $2")
      .replace('_', ' ')
      .replace('-', ' ')
      .trim()
      .replace(Regex("\\s+"), " ")
  }

  private fun AccessibilityNodeInfo.isEditableCompat(): Boolean {
    return try {
      this.isEditable
    } catch (_: Throwable) {
      false
    }
  }

  private fun AccessibilityNodeInfo.isVisibleCompat(): Boolean {
    return try {
      this.isVisibleToUser
    } catch (_: Throwable) {
      true
    }
  }

  private fun AccessibilityNodeInfo.hintTextCompat(): String {
    return try {
      this.hintText?.toString().orEmpty()
    } catch (_: Throwable) {
      ""
    }
  }
}

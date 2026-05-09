package ai.openclaw.androidbridge

data class UiNode(
  val nodeKey: String,
  val text: String,
  val contentDesc: String,
  val hintText: String,
  val resourceId: String,
  val className: String,
  val bounds: IntArray,
  val actions: List<String>,
  val role: String,
  val editable: Boolean,
  val scrollable: Boolean,
  val enabled: Boolean,
  val selected: Boolean,
  val checked: Boolean,
  val clickable: Boolean,
  val longClickable: Boolean,
  val checkable: Boolean,
  val focusable: Boolean,
  val focused: Boolean,
  val visible: Boolean,
  val childCount: Int,
  val packageName: String?,
  /** Lower rank = more foreground. 0=active window, 10=foreground pkg, 20=launcher, 30=noise, 40=other. */
  val windowRank: Int = 50,
  val windowType: String? = null,
  val activeWindow: Boolean = false,
  val depth: Int = 0,
  val siblingIndex: Int = 0,
  val parentKey: String? = null,
  val parentRole: String? = null,
  val parentLabel: String? = null,
  val semanticId: String = "",
  val semanticLabel: String = "",
  val labelSource: String = "",
  val containerKey: String? = null,
  val containerRole: String? = null,
  val containerLabel: String? = null,
  val sectionKey: String? = null,
  val sectionRole: String? = null,
  val sectionLabel: String? = null,
  val pathLabels: List<String> = emptyList(),
) {
  override fun equals(other: Any?): Boolean {
    if (this === other) return true
    if (javaClass != other?.javaClass) return false
    other as UiNode
    return nodeKey == other.nodeKey
  }

  override fun hashCode(): Int = nodeKey.hashCode()
}

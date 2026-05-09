package ai.openclaw.androidbridge

import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import androidx.appcompat.app.AppCompatActivity
import ai.openclaw.androidbridge.databinding.ActivityMainBinding

class MainActivity : AppCompatActivity() {
  private lateinit var binding: ActivityMainBinding

  override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)
    binding = ActivityMainBinding.inflate(layoutInflater)
    setContentView(binding.root)

    binding.openAccessibilityButton.setOnClickListener {
      startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
    }
    binding.refreshButton.setOnClickListener { renderStatus() }
    renderStatus()
  }

  override fun onResume() {
    super.onResume()
    renderStatus()
  }

  private fun renderStatus() {
    val serviceConnected = BridgeState.service != null
    val bridgeListening = BridgeState.bridgeListening
    BridgeState.service?.applyAllowedPackages(BridgeState.allowedPackages.toList())
    val allowedPackages = BridgeState.allowedPackages.joinToString(", ")
    binding.statusText.text = buildString {
      appendLine("Accessibility connected: $serviceConnected")
      appendLine("Bridge listening: $bridgeListening")
      appendLine("Bridge port: 49317")
      appendLine("Event seq: ${BridgeState.eventSeq.get()}")
      appendLine("Last package: ${BridgeState.lastPackage ?: "(none)"}")
      appendLine("Allowed packages: ${if (allowedPackages.isBlank()) "(all)" else allowedPackages}")
    }
  }
}

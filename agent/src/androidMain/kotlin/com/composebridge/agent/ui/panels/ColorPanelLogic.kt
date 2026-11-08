package com.composebridge.agent.ui.panels

import androidx.compose.ui.graphics.Color
import com.composebridge.agent.BridgeState
import com.composebridge.agent.DesignModeRegistry
import com.composebridge.agent.ElementColorInfo
import com.composebridge.agent.PendingEdit

/**
 * منطق الألوان، تحويلات Hex، وتسمية التوكنات التلقائية وقوائم الباليتات للوحة الألوان.
 * Color logic, hex conversion helpers, token name suggestions, and default palettes for ColorEditPanel.
 */

val DEFAULT_COLOR_PALETTE = listOf(
    "#1E3A8A", // Dark Blue
    "#3B82F6", // Blue
    "#10B981", // Green
    "#F59E0B", // Orange
    "#EF4444", // Red
    "#8B5CF6", // Purple
    "#EC4899", // Pink
    "#14B8A6", // Teal
    "#FFFFFF", // White
    "#1E293B", // Dark Slate
)

fun isValidKotlinIdentifier(name: String): Boolean {
    if (name.isEmpty()) return false
    if (!name[0].isLetter() && name[0] != '_') return false
    return name.all { it.isLetterOrDigit() || it == '_' }
}

fun hexToColorName(hex: String): String {
    val clean = hex.removePrefix("#").take(6)
    if (clean.length != 6) return "Custom"
    
    val r = clean.substring(0, 2).toIntOrNull(16) ?: return "Custom"
    val g = clean.substring(2, 4).toIntOrNull(16) ?: return "Custom"
    val b = clean.substring(4, 6).toIntOrNull(16) ?: return "Custom"
    
    return when {
        r > 230 && g > 230 && b > 230 -> "White"
        r < 30 && g < 30 && b < 30 -> "Black"
        kotlin.math.abs(r - g) < 20 && kotlin.math.abs(g - b) < 20 -> "Gray"
        g > r && g > b -> "Green"
        b > r && b > g -> "Blue"
        r > 200 && g in 100..180 && b < 100 -> "Orange"
        r > 200 && g > 200 && b < 100 -> "Yellow"
        r > 200 && b > 100 && g < 150 -> "Pink"
        r > 100 && b > 150 && g < r -> "Purple"
        g > 150 && b > 150 && r < 100 -> "Cyan"
        r > 180 && g < 100 && b < 100 -> "Red"
        else -> "Custom"
    }
}

fun suggestGlobalTokenName(
    color: ElementColorInfo,
    newHex: String,
): String {
    // ⭐ 1. If token exists and valid, use its name
    if (color.isToken && isValidKotlinIdentifier(color.name)) {
        return color.name
    }
    
    // ⭐ 2. Derive name from color.name
    val baseName = when {
        color.name.startsWith("Color(0x") -> ""
        color.name.startsWith("Color.") -> color.name.removePrefix("Color.")
        isValidKotlinIdentifier(color.name) -> color.name
        else -> ""
    }
    
    // ⭐ 3. Compute color name from newHex
    val colorFromHex = hexToColorName(newHex)
    
    // ⭐ 4. Compose name
    val candidate = if (baseName.isNotEmpty()) {
        "Success${baseName}${colorFromHex}"
    } else {
        "Success${colorFromHex}"
    }
    
    // ⭐ 5. Validate name
    return if (isValidKotlinIdentifier(candidate)) {
        candidate
    } else {
        "SuccessCustom${System.currentTimeMillis() % 1000}"
    }
}

fun suggestLocalTokenName(
    elementName: String,
    newHex: String,
    color: ElementColorInfo,
): String {
    // ⭐ Clean elementName
    val cleanElement = elementName
        .replace(Regex("[^A-Za-z0-9_]"), "")
        .ifEmpty { "Element" }
    
    val colorName = hexToColorName(newHex)
    val candidate = "${cleanElement}${colorName}Accent"
    
    return if (isValidKotlinIdentifier(candidate)) {
        candidate
    } else {
        "Accent${System.currentTimeMillis() % 1000}"
    }
}

fun propertyLabel(prop: String): String = when (prop.lowercase()) {
    "c1" -> "Primary Color (c1)"
    "c2" -> "Secondary Color (c2)"
    "background" -> "Background Color (background)"
    "color" -> "Color"
    "containercolor" -> "Container Color (containerColor)"
    "contentcolor" -> "Content Color (contentColor)"
    "tint" -> "Icon Tint (tint)"
    "textcolor" -> "Text Color (textColor)"
    "border" -> "Border Color (border)"
    else -> prop
}

fun parseHexColor(hex: String): Color = com.composebridge.agent.util.ColorUtils.parseHexColor(hex)

fun hexToColor(hex: String): Color = com.composebridge.agent.util.ColorUtils.hexToColor(hex)

fun performConvertHardcoded(
    color: ElementColorInfo,
    mode: String,  // "split" | "global"
) {
    if (BridgeState.syncInProgress) {
        BridgeState.showSnackbar("⏳ Sync in progress...")
        return
    }

    // ⭐ 1. Compute token name
    val elementName = BridgeState.elementComposableName
    val targetHex = BridgeState.hardcodedNewHex ?: color.value
    val suggestedName = if (mode == "split") {
        // ⭐ Dedicated to this element
        suggestLocalTokenName(elementName, targetHex, color)
    } else {
        // ⭐ Shared — generic name
        suggestGlobalTokenName(color, targetHex)
    }

    android.util.Log.i("ComposeBridge", "🎨 performConvertHardcoded:")
    android.util.Log.i("ComposeBridge", "   mode=$mode")
    android.util.Log.i("ComposeBridge", "   color=${color.name} ${color.value}")
    android.util.Log.i("ComposeBridge", "   suggestedName=$suggestedName")

    // ⭐ 2. Build occurrences JSON
    val occsJson = color.occurrencesInElement.joinToString(",", "[", "]") {
        """{"line":${it.line},"property":"${it.property}"}"""
    }

    // ⭐ 3. PendingEdit
    val edit = PendingEdit(
        id = java.util.UUID.randomUUID().toString(),
        type = if (mode == "split") "split_token" else "global_color",
        file = DesignModeRegistry.selectedSourceFile ?: "",
        line = color.lineInElement,
        property = color.property,
        oldValue = color.value,
        newValue = targetHex,  // ⭐ Value for conversion
        tokenName = suggestedName,
        timestamp = System.currentTimeMillis(),
        occurrencesJson = if (mode == "split") occsJson else null,
        occurrences = if (mode == "split") color.occurrencesInElement else emptyList(),
        originalToken = color.name,
        originalValue = color.value,
    )

    BridgeState.addPendingEdit(edit)
    BridgeState.hardcodedNewHex = null

    val modeText = if (mode == "split") "for this element" else "globally"
    BridgeState.showSnackbar(
        "📝 Added ($modeText) — ${BridgeState.pendingCount}\n⚠️ Rebuild required first time"
    )

    android.util.Log.i("ComposeBridge", "✅ Convert hardcoded added to queue")
}


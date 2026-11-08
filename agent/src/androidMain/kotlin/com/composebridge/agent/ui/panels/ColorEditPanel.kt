@file:OptIn(
    androidx.compose.foundation.ExperimentalFoundationApi::class,
    androidx.compose.material3.ExperimentalMaterial3Api::class,
)

/**
 * ComposeBridge Color Edit Panel.
 * لوحة فحص وتعديل ألوان عناصر واجهة المستخدم والتوكنات اللونية وتعديل الألوان المشتركة.
 */
package com.composebridge.agent.ui.panels

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.composebridge.agent.BridgeState
import com.composebridge.agent.DesignModeRegistry
import com.composebridge.agent.ElementColorInfo
import com.composebridge.agent.PendingEdit

/**
 * لوحة تعديل الألوان لعنصر Compose المحدد وإدارة الحوارات الخاصة بالألوان المشتركة.
 * Main Color Edit Panel composable displaying colors in the current element scope.
 */
@Composable
fun ColorEditPanel() {
    var colorPendingChange by remember { mutableStateOf<ElementColorInfo?>(null) }
    var pendingNewHex by remember { mutableStateOf<String?>(null) }

    Text(
        text = "🎨 Element Colors",
        fontSize = 14.sp,
        fontWeight = FontWeight.Bold,
        modifier = Modifier.padding(vertical = 4.dp)
    )

    if (BridgeState.elementAnalysisError != null) {
        Surface(
            color = Color(0xFFFFEBEE),
            shape = RoundedCornerShape(8.dp),
            modifier = Modifier
                .fillMaxWidth()
                .padding(vertical = 8.dp)
        ) {
            Text(
                text = "⚠️ ${BridgeState.elementAnalysisError}",
                fontSize = 12.sp,
                color = Color(0xFFC62828),
                modifier = Modifier.padding(10.dp)
            )
        }
    } else if (BridgeState.elementColors.isEmpty()) {
        Text(
            text = "No custom colors found in this element scope",
            fontSize = 12.sp,
            color = Color.Gray,
            modifier = Modifier.padding(vertical = 8.dp)
        )
    } else {
        BridgeState.elementColors.forEach { colorInfo ->
            if (colorInfo.isThemeDependent) {
                ThemeDependentColorCard(color = colorInfo)
            } else {
                FocusColorRow(
                    colorInfo = colorInfo,
                    onTapHeader = {
                        if (!colorInfo.isToken) {
                            BridgeState.hardcodedColorPending = colorInfo
                            BridgeState.hardcodedUsageGlobal = colorInfo.usageGlobal
                            BridgeState.hardcodedLocations = colorInfo.locations
                            BridgeState.hardcodedNewHex = null
                            BridgeState.showHardcodedDialog = true
                        }
                    },
                    onPickColor = { newHex ->
                        if (colorInfo.isToken) {
                            if (colorInfo.isShared) {
                                colorPendingChange = colorInfo
                                pendingNewHex = newHex
                            } else {
                                val edit = PendingEdit(
                                    type = "global_color",
                                    file = DesignModeRegistry.selectedSourceFile ?: "",
                                    line = colorInfo.lineInElement,
                                    property = colorInfo.property,
                                    oldValue = colorInfo.value,
                                    newValue = newHex,
                                    tokenName = colorInfo.name,
                                    originalToken = colorInfo.name,
                                    originalValue = colorInfo.value,
                                )
                                BridgeState.addPendingEdit(edit)
                                BridgeState.showSnackbar("📝 Added (${BridgeState.pendingCount})")
                            }
                        } else {
                            BridgeState.hardcodedColorPending = colorInfo
                            BridgeState.hardcodedUsageGlobal = colorInfo.usageGlobal
                            BridgeState.hardcodedLocations = colorInfo.locations
                            BridgeState.hardcodedNewHex = newHex
                            BridgeState.showHardcodedDialog = true
                        }
                    }
                )
            }
        }
    }

    // ═══ Shared Color Dialog ═══
    val pendingColor = colorPendingChange
    val newHex = pendingNewHex
    if (pendingColor != null && newHex != null) {
        SharedColorDialog(
            pendingColor = pendingColor,
            newHex = newHex,
            onDismiss = {
                colorPendingChange = null
                pendingNewHex = null
            }
        )
    }

    // ═══ Dialog: Convert Hardcoded to Token ═══
    val hardcodedColor = BridgeState.hardcodedColorPending
    if (BridgeState.showHardcodedDialog && hardcodedColor != null && !hardcodedColor.isThemeDependent) {
        HardcodedConvertDialog(
            hardcodedColor = hardcodedColor,
            onDismiss = {
                BridgeState.showHardcodedDialog = false
                BridgeState.hardcodedColorPending = null
                BridgeState.hardcodedNewHex = null
            }
        )
    }
}

@Composable
private fun ThemeDependentColorCard(
    color: ElementColorInfo,
) {
    Surface(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 6.dp)
            .clickable {
                BridgeState.showSnackbar("⚠️ This color follows app theme — cannot be edited directly")
            },
        shape = RoundedCornerShape(8.dp),
        color = Color(0xFFF3F4F6),
    ) {
        Column(
            modifier = Modifier.padding(12.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("🎨", fontSize = 18.sp)
                Spacer(Modifier.width(8.dp))
                Column {
                    Text(
                        text = propertyLabel(color.property),
                        fontWeight = FontWeight.SemiBold,
                        fontSize = 14.sp,
                        color = Color(0xFF1E293B),
                    )
                    Text(
                        text = color.name,
                        fontSize = 11.sp,
                        fontFamily = FontFamily.Monospace,
                        color = Color.Gray,
                    )
                }
            }
            Spacer(Modifier.height(8.dp))
            Row(
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("⚠️", fontSize = 14.sp)
                Spacer(Modifier.width(4.dp))
                Text(
                    text = "Follows app theme — updates automatically",
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Bold,
                    color = Color(0xFFE65100),
                )
            }
            Text(
                text = "For independent edits, use Split after converting",
                fontSize = 10.sp,
                color = Color.Gray,
                modifier = Modifier.padding(top = 4.dp),
            )
        }
    }
}

@Composable
private fun FocusColorRow(
    colorInfo: ElementColorInfo,
    onTapHeader: (() -> Unit)? = null,
    onPickColor: (String) -> Unit,
) {
    if (colorInfo.isThemeDependent) {
        ThemeDependentColorCard(color = colorInfo)
        return
    }

    val currentEffectiveHex = BridgeState.previewColors[colorInfo.name] ?: colorInfo.value
    var selectedHex by remember(currentEffectiveHex) { mutableStateOf(currentEffectiveHex) }

    val basePalette = DEFAULT_COLOR_PALETTE

    val palette = remember(colorInfo.value) {
        val clean = colorInfo.value.trim().uppercase()
        if (clean.startsWith("#") && basePalette.none { it.equals(clean, ignoreCase = true) }) {
            listOf(clean) + basePalette
        } else {
            basePalette
        }
    }

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 10.dp)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .then(
                    if (!colorInfo.isToken) Modifier.clickable { onTapHeader?.invoke() }
                    else Modifier
                ),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                Text(
                    text = "${propertyLabel(colorInfo.property)}:",
                    fontSize = 14.sp,
                    fontWeight = FontWeight.Bold,
                    color = Color(0xFF1E293B)
                )

                if (!colorInfo.isToken) {
                    Text(
                        text = colorInfo.name,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.SemiBold,
                        fontFamily = FontFamily.Monospace,
                        color = Color(0xFF475569)
                    )
                }

                when {
                    colorInfo.isToken && colorInfo.isShared -> {
                        Surface(
                            shape = RoundedCornerShape(4.dp),
                            color = Color(0xFF6366F1).copy(alpha = 0.12f),
                        ) {
                            Text(
                                text = "🔗 Shared Token (${colorInfo.usageGlobal})",
                                fontSize = 10.sp,
                                color = Color(0xFF6366F1),
                                fontWeight = FontWeight.Bold,
                                modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                            )
                        }
                    }
                    colorInfo.isToken -> {
                        Surface(
                            shape = RoundedCornerShape(4.dp),
                            color = Color(0xFF10B981).copy(alpha = 0.12f),
                        ) {
                            Text(
                                text = "🔗 Standalone Token",
                                fontSize = 10.sp,
                                color = Color(0xFF10B981),
                                fontWeight = FontWeight.Bold,
                                modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                            )
                        }
                    }
                    else -> {
                        Surface(
                            shape = RoundedCornerShape(4.dp),
                            color = Color(0xFFE65100).copy(alpha = 0.12f),
                            modifier = Modifier.clickable {
                                onTapHeader?.invoke()
                            }
                        ) {
                            Text(
                                text = "⚠️ Hardcoded — Tap to Convert",
                                fontSize = 10.sp,
                                color = Color(0xFFE65100),
                                fontWeight = FontWeight.SemiBold,
                                modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                            )
                        }
                    }
                }
            }

            Text(
                text = selectedHex,
                fontSize = 12.sp,
                fontWeight = FontWeight.Bold,
                color = Color(0xFF6366F1)
            )
        }

        Spacer(Modifier.height(8.dp))

        Row(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState())
                .padding(vertical = 4.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            palette.forEach { hex ->
                val isSelected = hex.equals(selectedHex, ignoreCase = true)
                val isWhiteOrLight = hex.equals("#FFFFFF", ignoreCase = true) || hex.equals("#F1F5F9", ignoreCase = true)
                Box(
                    modifier = Modifier
                        .size(40.dp)
                        .clip(CircleShape)
                        .background(hexToColor(hex))
                        .border(
                            width = if (isSelected) 3.dp else 1.dp,
                            color = if (isSelected) Color(0xFF6366F1) else Color.Gray.copy(alpha = 0.3f),
                            shape = CircleShape
                        )
                        .clickable {
                            selectedHex = hex
                            onPickColor(hex)
                        },
                    contentAlignment = Alignment.Center
                ) {
                    if (isSelected) {
                        Icon(
                            imageVector = Icons.Default.Check,
                            contentDescription = "Selected",
                            tint = if (isWhiteOrLight) Color(0xFF1E293B) else Color.White,
                            modifier = Modifier.size(20.dp)
                        )
                    }
                }
            }
        }
    }
}

@file:OptIn(
    androidx.compose.material3.ExperimentalMaterial3Api::class,
)

package com.composebridge.agent.ui.panels

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
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
 * نوافذ حوار تعديل الألوان المشتركة وتحويل الألوان الثابتة إلى توكنات.
 * Dialog composables for shared color splits, global edits, and hardcoded-to-token conversions.
 */

@Composable
fun SharedColorDialog(
    pendingColor: ElementColorInfo,
    newHex: String,
    onDismiss: () -> Unit,
) {
    val isSharedValue = (pendingColor.isShared && pendingColor.usageGlobal > 1) || pendingColor.usageGlobal > 1
    if (isSharedValue) {
        val locations = pendingColor.locations
        AlertDialog(
            onDismissRequest = onDismiss,
            title = {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("🔗", fontSize = 24.sp)
                    Spacer(Modifier.width(8.dp))
                    Text("Shared Value", fontWeight = FontWeight.Bold, fontSize = 18.sp)
                }
            },
            text = {
                Column(
                    modifier = Modifier
                        .heightIn(max = 400.dp)
                        .verticalScroll(rememberScrollState()),
                ) {
                    Text(
                        text = "Color '${pendingColor.name}' (${pendingColor.value})",
                        fontWeight = FontWeight.SemiBold,
                    )
                    Spacer(Modifier.height(4.dp))
                    Text(
                        text = "Used in ${pendingColor.usageGlobal} occurrences in project",
                        color = Color(0xFFE65100),
                        fontWeight = FontWeight.Bold,
                    )

                    Spacer(Modifier.height(12.dp))
                    HorizontalDivider()
                    Spacer(Modifier.height(12.dp))

                    Text(
                        text = "📍 Locations:",
                        fontWeight = FontWeight.SemiBold,
                        fontSize = 14.sp,
                    )
                    Spacer(Modifier.height(8.dp))

                    if (locations.isNotEmpty()) {
                        locations.take(10).forEach { loc ->
                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .padding(vertical = 4.dp, horizontal = 8.dp),
                            ) {
                                Text("•", color = Color(0xFF6366F1))
                                Spacer(Modifier.width(6.dp))
                                Column {
                                    Text(
                                        text = "${loc.file}:${loc.line}",
                                        fontSize = 12.sp,
                                        fontFamily = FontFamily.Monospace,
                                        color = Color(0xFF6366F1),
                                    )
                                    if (loc.property.isNotEmpty()) {
                                        Text(
                                            text = "  property: ${loc.property}",
                                            fontSize = 11.sp,
                                            color = Color.Gray,
                                        )
                                    }
                                }
                            }
                        }

                        if (locations.size > 10) {
                            Text(
                                text = "... and ${locations.size - 10} more locations",
                                fontSize = 12.sp,
                                color = Color.Gray,
                                modifier = Modifier.padding(start = 20.dp, top = 4.dp),
                            )
                        }
                    } else {
                        Text(
                            text = "Appears across multiple files in the project",
                            fontSize = 12.sp,
                            color = Color.Gray,
                            modifier = Modifier.padding(start = 8.dp),
                        )
                    }

                    Spacer(Modifier.height(16.dp))
                    HorizontalDivider()
                    Spacer(Modifier.height(12.dp))

                    Text("🔀 Split for This Element (Recommended):", fontWeight = FontWeight.SemiBold, fontSize = 13.sp, color = Color(0xFF10B981))
                    Text("Creates a new dedicated token for this element only.\nOther occurrences retain the original color.", fontSize = 11.sp, color = Color.Gray)
                    Spacer(Modifier.height(12.dp))
                    Text("🌍 Edit Globally:", fontWeight = FontWeight.SemiBold, fontSize = 13.sp, color = Color(0xFFE65100))
                    Text("Updates all ${pendingColor.usageGlobal} occurrences.\n⚠️ Affects the entire application!", fontSize = 11.sp, color = Color.Gray)
                }
            },
            confirmButton = {
                Button(
                    onClick = {
                        if (BridgeState.syncInProgress) return@Button
                        val newTokenName = suggestLocalTokenName(BridgeState.elementComposableName, newHex, pendingColor)
                        val edit = PendingEdit(
                            type = "split_token",
                            file = DesignModeRegistry.selectedSourceFile ?: "",
                            line = pendingColor.lineInElement,
                            property = pendingColor.property,
                            oldValue = pendingColor.value,
                            newValue = newHex,
                            tokenName = newTokenName,
                            originalToken = if (pendingColor.isToken) pendingColor.name else pendingColor.value,
                            occurrences = pendingColor.occurrencesInElement,
                        )
                        BridgeState.addPendingEdit(edit)
                        BridgeState.showSnackbar("✂️ Split token ($newTokenName)")
                        onDismiss()
                    },
                    colors = ButtonDefaults.buttonColors(
                        containerColor = Color(0xFF10B981),
                    )
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("🔀 Split for This Element", fontWeight = FontWeight.Bold, fontSize = 14.sp)
                        Text("Safe — does not affect others", fontSize = 10.sp)
                    }
                }
            },
            dismissButton = {
                Row(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    OutlinedButton(
                        onClick = {
                            if (BridgeState.syncInProgress) return@OutlinedButton
                            val tokenName = if (pendingColor.isToken && isValidKotlinIdentifier(pendingColor.name)) {
                                pendingColor.name
                            } else {
                                suggestGlobalTokenName(pendingColor, newHex)
                            }
                            val edit = PendingEdit(
                                type = "global_color",
                                file = DesignModeRegistry.selectedSourceFile ?: "",
                                line = pendingColor.lineInElement,
                                property = "all",
                                oldValue = pendingColor.value,
                                newValue = newHex,
                                tokenName = tokenName,
                            )
                            BridgeState.addPendingEdit(edit)
                            BridgeState.showSnackbar("🌍 Global edit added (${pendingColor.usageGlobal} occurrences)")
                            onDismiss()
                        },
                        colors = ButtonDefaults.outlinedButtonColors(
                            contentColor = Color(0xFFE65100),
                        )
                    ) {
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Text("🌍 Edit Globally", fontSize = 12.sp, fontWeight = FontWeight.Bold)
                            Text("⚠️ ${pendingColor.usageGlobal} places", fontSize = 10.sp)
                        }
                    }
                    TextButton(onClick = onDismiss) {
                        Text("Cancel", fontSize = 12.sp)
                    }
                }
            }
        )
    } else if (pendingColor.isToken) {
        AlertDialog(
            onDismissRequest = onDismiss,
            title = {
                Text(
                    text = "🔗 Edit Token",
                    fontWeight = FontWeight.Bold,
                    fontSize = 17.sp
                )
            },
            text = {
                Text(
                    text = "Color '${pendingColor.name}' (${pendingColor.value}) exists as a token.\n\nUpdating its value will affect this element.",
                    fontSize = 14.sp
                )
            },
            confirmButton = {
                Button(
                    onClick = {
                        val edit = PendingEdit(
                            type = "global_color",
                            file = DesignModeRegistry.selectedSourceFile ?: "",
                            line = pendingColor.lineInElement,
                            property = "all",
                            oldValue = pendingColor.value,
                            newValue = newHex,
                            tokenName = pendingColor.name,
                        )
                        BridgeState.addPendingEdit(edit)
                        BridgeState.showSnackbar("📝 Added (${BridgeState.pendingCount})")
                        onDismiss()
                    },
                    colors = ButtonDefaults.buttonColors(
                        containerColor = Color(0xFF6366F1)
                    )
                ) {
                    Text("Update Value", fontWeight = FontWeight.Bold)
                }
            },
            dismissButton = {
                TextButton(onClick = onDismiss) {
                    Text("Cancel", fontSize = 12.sp)
                }
            }
        )
    }
}

@Composable
fun HardcodedConvertDialog(
    hardcodedColor: ElementColorInfo,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("🎨", fontSize = 24.sp)
                Spacer(Modifier.width(8.dp))
                Text("Hardcoded Color", fontWeight = FontWeight.Bold, fontSize = 17.sp)
            }
        },
        text = {
            Column(
                modifier = Modifier
                    .heightIn(max = 400.dp)
                    .verticalScroll(rememberScrollState()),
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Box(
                        modifier = Modifier
                            .size(32.dp)
                            .background(
                                hexToColor(hardcodedColor.value),
                                CircleShape,
                            )
                    )
                    Spacer(Modifier.width(12.dp))
                    Column {
                        Text(
                            text = hardcodedColor.name,
                            fontFamily = FontFamily.Monospace,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.SemiBold,
                        )
                        Text(
                            text = hardcodedColor.value,
                            fontSize = 12.sp,
                            color = Color.Gray,
                        )
                    }
                }

                Spacer(Modifier.height(12.dp))
                HorizontalDivider()
                Spacer(Modifier.height(12.dp))

                Text(
                    text = "⚠️ This color is hardcoded directly in source code",
                    fontSize = 13.sp,
                    color = Color(0xFFE65100),
                    fontWeight = FontWeight.SemiBold,
                )
                Spacer(Modifier.height(6.dp))
                Text(
                    text = "To edit it, it must first be converted into a token.",
                    fontSize = 12.sp,
                    color = Color.Gray,
                )

                if (hardcodedColor.usageGlobal > 1) {
                    Spacer(Modifier.height(12.dp))
                    Text(
                        text = "📍 Used in ${hardcodedColor.usageGlobal} occurrences",
                        fontSize = 12.sp,
                        fontWeight = FontWeight.SemiBold,
                    )

                    hardcodedColor.locations.take(5).forEach { loc ->
                        Row(
                            modifier = Modifier.padding(
                                start = 16.dp,
                                top = 2.dp,
                            ),
                        ) {
                            Text("•", color = Color(0xFF6366F1), fontSize = 11.sp)
                            Spacer(Modifier.width(4.dp))
                            Text(
                                text = "${loc.file.substringAfterLast("/")}:${loc.line}",
                                fontSize = 11.sp,
                                fontFamily = FontFamily.Monospace,
                                color = Color(0xFF6366F1),
                            )
                        }
                    }

                    if (hardcodedColor.locations.size > 5) {
                        Text(
                            text = "... and ${hardcodedColor.locations.size - 5} more locations",
                            fontSize = 11.sp,
                            color = Color.Gray,
                            modifier = Modifier.padding(start = 20.dp, top = 2.dp),
                        )
                    }
                }

                Spacer(Modifier.height(12.dp))
                HorizontalDivider()
                Spacer(Modifier.height(12.dp))

                Text(
                    text = "🔀 Convert for This Element (Recommended):",
                    fontSize = 12.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = Color(0xFF10B981),
                )
                Text(
                    text = "Creates a new token dedicated to this element only.",
                    fontSize = 11.sp,
                    color = Color.Gray,
                )

                Spacer(Modifier.height(8.dp))

                Text(
                    text = "🌍 Convert Globally:",
                    fontSize = 12.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = Color(0xFFE65100),
                )
                Text(
                    text = "Replaces all ${hardcodedColor.usageGlobal} occurrences with a shared token.",
                    fontSize = 11.sp,
                    color = Color.Gray,
                )

                Spacer(Modifier.height(12.dp))
                HorizontalDivider()
                Spacer(Modifier.height(8.dp))

                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(
                            Color(0xFFFFF3E0),
                            RoundedCornerShape(8.dp),
                        )
                        .padding(8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text("⚠️", fontSize = 16.sp)
                    Spacer(Modifier.width(6.dp))
                    Text(
                        text = "Initial conversion requires a rebuild to apply. Subsequent edits will be instant.",
                        fontSize = 11.sp,
                        color = Color(0xFFE65100),
                    )
                }
            }
        },
        confirmButton = {
            Column(
                modifier = Modifier.fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                Button(
                    onClick = {
                        performConvertHardcoded(
                            color = hardcodedColor,
                            mode = "split",
                        )
                        onDismiss()
                    },
                    colors = ButtonDefaults.buttonColors(
                        containerColor = Color(0xFF10B981),
                    ),
                    shape = RoundedCornerShape(10.dp),
                    contentPadding = PaddingValues(horizontal = 12.dp, vertical = 6.dp),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(
                            "🔀 Convert for This Element",
                            fontWeight = FontWeight.Bold,
                            fontSize = 13.sp,
                        )
                        Text(
                            "Safe — does not affect others",
                            fontSize = 10.sp,
                        )
                    }
                }

                OutlinedButton(
                    onClick = {
                        performConvertHardcoded(
                            color = hardcodedColor,
                            mode = "global",
                        )
                        onDismiss()
                    },
                    colors = ButtonDefaults.outlinedButtonColors(
                        contentColor = Color(0xFFE65100),
                    ),
                    shape = RoundedCornerShape(10.dp),
                    contentPadding = PaddingValues(horizontal = 12.dp, vertical = 6.dp),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(
                            "🌍 Convert Globally",
                            fontWeight = FontWeight.Bold,
                            fontSize = 12.sp,
                        )
                        Text(
                            "⚠️ ${hardcodedColor.usageGlobal} places",
                            fontSize = 10.sp,
                        )
                    }
                }

                TextButton(
                    onClick = onDismiss,
                    contentPadding = PaddingValues(vertical = 2.dp),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text("Cancel", fontSize = 12.sp, color = Color.Gray)
                }
            }
        },
        dismissButton = null
    )
}

"""Qt (PySide6) user interface: the Python counterpart of the SwiftUI views
(ContentView, DeviceListView, EffectsChainView) and of MooerPrimeMacApp.

The protocol objects notify `listeners` on every change; the window then
schedules one coalesced refresh. Rows are updated in place when their
structure is unchanged, so a slider being dragged is never rebuilt.
"""

from __future__ import annotations

import sys
from datetime import datetime
from typing import Callable, List, Optional

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QSlider, QSpinBox, QSplitter, QStackedWidget, QStyle,
    QTableWidget, QToolButton, QVBoxLayout, QWidget,
)

from . import catalog
from .bluetooth import CONNECTED, DISCONNECTED, SCANNING, BluetoothManager
from .models import NAME_LENGTH, PrimeModule, slot_label
from .protocol import LINK_USB, PrimeM2Device
from .usb import PrimeUSBDevice

EM_DASH = "—"
APP_TITLE = f"Mooer Prime (Python) {EM_DASH} unofficial"


# ---- Plumbing ---------------------------------------------------------------

def log(line: str) -> None:
    print(f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] {line}", flush=True)


class _Timer:
    def __init__(self, seconds: float, fn: Callable[[], None], scheduler: "QtScheduler") -> None:
        self._scheduler = scheduler
        self._timer = QTimer(scheduler)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fire)
        self._fn = fn
        self._timer.start(max(0, int(seconds * 1000)))

    def _fire(self) -> None:
        self._release()
        self._fn()

    def cancel(self) -> None:
        self._timer.stop()
        self._release()

    def _release(self) -> None:
        self._scheduler.active.discard(self)
        self._timer.deleteLater()


class QtScheduler(QObject):
    """`call_later` for the protocol objects, on the GUI thread."""

    def __init__(self) -> None:
        super().__init__()
        # PySide only weakly references bound-method slots, so pending timers
        # are kept here until they fire or are cancelled.
        self.active: set = set()

    def call_later(self, seconds: float, fn: Callable[[], None]) -> _Timer:
        timer = _Timer(seconds, fn, self)
        self.active.add(timer)
        return timer


class MainThreadInvoker(QObject):
    """Runs callables posted from any thread on the GUI thread."""

    invoke = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.invoke.connect(lambda fn: fn(), Qt.QueuedConnection)

    def post(self, fn: Callable[[], None]) -> None:
        self.invoke.emit(fn)


def mono_font(point_delta: int = 0) -> QFont:
    font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    if point_delta:
        font.setPointSize(max(6, font.pointSize() + point_delta))
    return font


def secondary(label: QLabel) -> QLabel:
    label.setStyleSheet("color: palette(placeholder-text);")
    return label


def small(widget: QWidget, delta: int = -1) -> QWidget:
    font = widget.font()
    font.setPointSize(max(6, font.pointSize() + delta))
    widget.setFont(font)
    return widget


def unnamed_icon() -> QIcon:
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("orange"))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(1, 1, 14, 14)
    painter.setPen(QColor("white"))
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(11)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "?")
    painter.end()
    return QIcon(pixmap)


def blank_icon() -> QIcon:
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.transparent)
    return QIcon(pixmap)


def category_menu(parent: QWidget, on_pick: Callable[[int], None]) -> QMenu:
    """Category picker shared by used and empty slots, in the official app's order."""
    menu = QMenu(parent)
    for kind in catalog.CATEGORY_ORDER:
        menu.addAction(catalog.type_name(kind)).triggered.connect(lambda _=False, k=kind: on_pick(k))
    return menu


def menu_button(title: str, menu: QMenu, width: Optional[int] = None) -> QPushButton:
    button = QPushButton(title)
    button.setMenu(menu)
    if width:
        button.setFixedWidth(width)
    else:
        button.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
    return button


# ---- Device list (DeviceListView) ------------------------------------------

class DeviceListPanel(QWidget):
    def __init__(self, bluetooth: BluetoothManager) -> None:
        super().__init__()
        self.bluetooth = bluetooth
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        title = QLabel("Nearby Bluetooth devices")
        title_font = title.font()
        title_font.setBold(True)
        title.setFont(title_font)
        header.addWidget(title)
        header.addStretch()
        self.scan_button = QPushButton("Scan")
        self.scan_button.clicked.connect(self._toggle_scan)
        header.addWidget(self.scan_button)
        layout.addLayout(header)

        self.prime_only = QCheckBox("Only devices named “Prime”")
        self.prime_only.setChecked(True)
        self.prime_only.toggled.connect(lambda _: self.refresh())
        layout.addWidget(small(self.prime_only, 0))

        self.power_label = secondary(QLabel("Bluetooth is off or not authorized. Enable it in System Settings."))
        self.power_label.setWordWrap(True)
        layout.addWidget(self.power_label)

        self.table = QTableWidget(0, 3)
        self.table.horizontalHeader().hide()
        self.table.verticalHeader().hide()
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.setMinimumHeight(160)
        layout.addWidget(self.table, 1)

        self.hidden_label = small(secondary(QLabel()))
        layout.addWidget(self.hidden_label)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        layout.addWidget(line)

        self.connection_label = QLabel()
        layout.addWidget(self.connection_label)
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.clicked.connect(bluetooth.disconnect)
        layout.addWidget(self.disconnect_button, 0, Qt.AlignLeft)
        self._rows: list = []

    def _toggle_scan(self) -> None:
        if self.bluetooth.connection_state == SCANNING:
            self.bluetooth.stop_scan()
        else:
            self.bluetooth.start_scan()

    def refresh(self) -> None:
        bt = self.bluetooth
        self.scan_button.setText("Stop" if bt.connection_state == SCANNING else "Scan")
        self.scan_button.setEnabled(bt.available)
        self.power_label.setVisible(not bt.is_powered_on)

        visible = [d for d in bt.discovered.values()
                   if not self.prime_only.isChecked() or "prime" in d.name.lower()]
        visible.sort(key=lambda d: d.rssi, reverse=True)
        can_connect = bt.connection_state in (DISCONNECTED, SCANNING)
        rows = [(d.id, d.name, d.rssi, can_connect) for d in visible]
        if rows != self._rows:
            self._rows = rows
            self.table.setRowCount(len(visible))
            for row, item in enumerate(visible):
                name = QLabel(f"<b>{item.name}</b><br><small>{item.id}</small>")
                name.setTextFormat(Qt.RichText)
                self.table.setCellWidget(row, 0, name)
                self.table.setCellWidget(row, 1, small(secondary(QLabel(f"{item.rssi} dBm"))))
                button = QPushButton("Connect")
                # One peripheral at a time: disconnect before connecting elsewhere.
                button.setEnabled(can_connect)
                button.clicked.connect(lambda _=False, i=item: bt.connect(i))
                self.table.setCellWidget(row, 2, button)
            self.table.resizeRowsToContents()

        hidden = not visible and bt.discovered
        self.hidden_label.setText(f"{len(bt.discovered)} device(s) hidden by the Prime filter" if hidden else "")
        self.hidden_label.setVisible(bool(hidden))
        name = f" — {bt.connected_name}" if bt.connected_name else ""
        self.connection_label.setText(f"Connection: {bt.connection_state}{name}")
        self.disconnect_button.setVisible(bt.connection_state != DISCONNECTED)


# ---- Module rows (ModuleRow / EmptySlotRow) --------------------------------

def _control_kind(param: catalog.Param, value: int):
    if param.syncable and catalog.sync_index(param.kind, value) is not None:
        return ("sync",)
    rng = catalog.slider_range(param, value)
    return ("slider", rng) if rng else ("field",)


def _visible_parameter_indices(module: PrimeModule) -> List[int]:
    """Labelled parameters only; unlabelled words are values the official app
    hides too, shown read-only in the raw line instead."""
    return [i for i in module.parameter_indices
            if catalog.param(module.type, module.model, i - 2).kind != catalog.ParamKind.UNKNOWN]


def _structure(module: PrimeModule):
    indices = _visible_parameter_indices(module)
    controls = tuple(_control_kind(catalog.param(module.type, module.model, i - 2), module.words[i])
                     for i in indices)
    return module.slot, module.type, module.model, len(module.words), tuple(indices), controls


class ModuleRow(QFrame):
    """One used slot: on/off, category and model pickers, remove, and every
    labelled parameter word. Picking a category or model goes through `2d`
    (pedal defaults, DSP check); sliders and on/off through `25`."""

    def __init__(self, module: PrimeModule, on_change, on_place, on_remove) -> None:
        super().__init__()
        self.module = module
        self.structure = _structure(module)
        self.on_change = on_change
        self.on_place = on_place
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        header = QHBoxLayout()
        self.enabled = QCheckBox(f"{module.slot}.")
        bold = self.enabled.font()
        bold.setBold(True)
        self.enabled.setFont(bold)
        self.enabled.toggled.connect(self._toggle_enabled)
        header.addWidget(self.enabled)

        def pick_category(kind: int) -> None:
            if kind != self.module.type:
                models = catalog.models_of(kind)
                on_place(kind, models[0] if models else 0)

        self.category = menu_button(module.type_name, category_menu(self, pick_category), 120)
        header.addWidget(self.category)

        model_menu = QMenu(self)
        for model in catalog.models_of(module.type):
            model_menu.addAction(catalog.model_label(module.type, model)).triggered.connect(
                lambda _=False, m=model: m != self.module.model and on_place(self.module.type, m))
        self.model = menu_button("", model_menu)
        self.model.setMaximumWidth(230)
        header.addWidget(self.model)
        header.addStretch()
        trash = QToolButton()
        trash.setIcon(self.style().standardIcon(QStyle.SP_TrashIcon))
        trash.setAutoRaise(True)
        trash.setToolTip("Remove this effect (later effects move up). Save to keep the change")
        trash.clicked.connect(on_remove)
        header.addWidget(trash)
        layout.addLayout(header)

        grid = QGridLayout()
        grid.setContentsMargins(28, 0, 0, 0)
        grid.setColumnStretch(1, 1)
        self.controls = {}
        for row, word_index in enumerate(self.structure[4]):
            self._add_parameter(grid, row, word_index)
        layout.addLayout(grid)

        self.raw = small(secondary(QLabel()), -2)
        self.raw.setFont(mono_font(-2))
        self.raw.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.raw.setContentsMargins(28, 0, 0, 0)
        layout.addWidget(self.raw)
        self.set_module(module)

    def _add_parameter(self, grid: QGridLayout, row: int, word_index: int) -> None:
        param = catalog.param(self.module.type, self.module.model, word_index - 2)
        value = self.module.words[word_index]
        label_box = QHBoxLayout()
        label_box.setSpacing(4)
        label_box.addWidget(QLabel(param.label))
        label_box.addStretch()
        sync_button = None
        if param.syncable:
            sync_button = QToolButton()
            sync_button.setText("♪")
            sync_button.setCheckable(True)
            sync_button.setAutoRaise(True)
            sync_button.clicked.connect(lambda _=False, w=word_index, p=param: self._toggle_sync(w, p))
            label_box.addWidget(sync_button)
        label_widget = QWidget()
        label_widget.setLayout(label_box)
        label_box.setContentsMargins(0, 0, 0, 0)
        label_widget.setFixedWidth(110)
        grid.addWidget(label_widget, row, 0)

        kind = _control_kind(param, value)
        value_label = QLabel()
        value_label.setFont(mono_font(-1))
        if kind[0] == "sync":
            # Note slider in the official app's order; stores the note's index.
            control = QSlider(Qt.Horizontal)
            control.setRange(0, len(catalog.SYNC_NOTE_ORDER) - 1)
            control.setPageStep(1)
            control.valueChanged.connect(lambda pos, w=word_index, p=param: self._set(
                w, catalog.synced_value(p.kind, catalog.SYNC_NOTE_ORDER[pos][0])))
            value_label.setFixedWidth(70)
            value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(control, row, 1)
            grid.addWidget(value_label, row, 2)
        elif kind[0] == "slider":
            control = QSlider(Qt.Horizontal)
            control.setRange(*kind[1])
            control.valueChanged.connect(lambda v, w=word_index: self._set(w, v))
            value_label.setFixedWidth(70)
            value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(control, row, 1)
            grid.addWidget(value_label, row, 2)
        else:
            control = QSpinBox()
            control.setRange(0, 0xFFFF)
            control.setKeyboardTracking(False)
            control.setFixedWidth(80)
            control.valueChanged.connect(lambda v, w=word_index: self._set(w, v))
            secondary(value_label)
            box = QHBoxLayout()
            box.addWidget(control)
            box.addWidget(value_label)
            box.addStretch()
            holder = QWidget()
            holder.setLayout(box)
            box.setContentsMargins(0, 0, 0, 0)
            grid.addWidget(holder, row, 1, 1, 2)
        self.controls[word_index] = (param, kind[0], control, value_label, sync_button)

    def set_module(self, module: PrimeModule) -> None:
        """Updates values in place (structure is unchanged)."""
        self.module = module
        self.enabled.blockSignals(True)
        self.enabled.setChecked(module.is_enabled)
        self.enabled.blockSignals(False)
        self.category.setText(module.type_name)
        self.model.setText(catalog.model_label(module.type, module.model) if module.model is not None else "—")
        for word_index, (param, kind, control, value_label, sync_button) in self.controls.items():
            value = module.words[word_index]
            value_label.setText(catalog.format_value(value, param.kind))
            if sync_button is not None:
                synced = catalog.sync_index(param.kind, value) is not None
                sync_button.setChecked(synced)
                sync_button.setToolTip("Synced to the tempo. Click to set a free time" if synced
                                       else "Click to sync to the tempo")
            if kind == "sync":
                index = catalog.sync_index(param.kind, value)
                target = next((i for i, (n, _) in enumerate(catalog.SYNC_NOTE_ORDER) if n == index), 0)
            else:
                target = value
            busy = control.isSliderDown() if isinstance(control, QSlider) else control.hasFocus()
            if not busy and control.value() != target:
                control.blockSignals(True)
                control.setValue(target)
                control.blockSignals(False)
        self.raw.setText(" ".join(map(str, module.words)))

    def _toggle_enabled(self, enabled: bool) -> None:
        self.module = self.module.with_enabled(enabled)
        self.on_change(self.module)

    def _toggle_sync(self, word_index: int, param: catalog.Param) -> None:
        """Toggles tempo sync: on -> 1/4, off -> 500 ms (Delay Time) or 50 (Mod Rate)."""
        synced = catalog.sync_index(param.kind, self.module.words[word_index]) is not None
        if synced:
            self._set(word_index, 50 if param.kind == catalog.ParamKind.RATE else 500)
        else:
            self._set(word_index, catalog.synced_value(param.kind, 10))

    def _set(self, word_index: int, value: int) -> None:
        if self.module.words[word_index] == value:
            return
        # Keep the latest copy so quick edits of different words don't undo each other.
        self.module = self.module.with_word(word_index, value)
        self.on_change(self.module)


class EmptySlotRow(QWidget):
    """An unused slot. Only the first one can take an effect, since the pedal
    keeps effects packed from slot 1; the rest are placeholders."""

    def __init__(self, number: int, is_next: bool, on_add: Callable[[int], None]) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        label = secondary(QLabel(f"<b>{number}.</b>"))
        layout.addWidget(label)
        if is_next:
            layout.addWidget(menu_button("Add effect…", category_menu(self, on_add), 140))
        else:
            layout.addWidget(secondary(QLabel("Empty")))
        layout.addStretch()


# ---- Effects chain (EffectsChainView) --------------------------------------

class EffectsChainPanel(QWidget):
    def __init__(self, device: PrimeM2Device) -> None:
        super().__init__()
        self.device = device
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Status bar
        bar = QHBoxLayout()
        bar.setContentsMargins(12, 6, 12, 6)
        bar.setSpacing(16)
        self.firmware = small(QLabel())
        self.battery = small(QLabel())
        self.tempo = small(QLabel())
        self.serial = small(secondary(QLabel()))
        self.link = small(QLabel())
        for widget in (self.firmware, self.battery, self.tempo, self.serial, self.link):
            bar.addWidget(widget)
        bar.addStretch()
        self.scanning = small(secondary(QLabel()))
        bar.addWidget(self.scanning)
        # Fixed slot so showing/hiding the spinner never shifts the buttons.
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setTextVisible(False)
        self.busy.setFixedSize(40, 10)
        policy = self.busy.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.busy.setSizePolicy(policy)
        bar.addWidget(self.busy)
        self.copy_button = small(QPushButton("Copy raw dump"))
        self.copy_button.setToolTip("Copies every scanned preset's raw module words, "
                                    "for decoding against the official app")
        self.copy_button.clicked.connect(self._copy_raw_dump)
        bar.addWidget(self.copy_button)
        layout.addLayout(bar)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        layout.addWidget(line)

        self.stack = QStackedWidget()
        self.message = secondary(QLabel())
        self.message.setAlignment(Qt.AlignCenter)
        self.message.setWordWrap(True)
        self.stack.addWidget(self.message)

        split = QSplitter(Qt.Horizontal)
        self.preset_list = QListWidget()
        self.preset_list.setIconSize(QSize(16, 16))
        self._unnamed_icon, self._blank_icon = unnamed_icon(), blank_icon()
        for index in range(1, 81):
            item = QListWidgetItem(self._blank_icon, slot_label(index))
            self.preset_list.addItem(item)
        self.preset_list.currentRowChanged.connect(self._preset_clicked)
        self.preset_list.setMinimumWidth(200)
        self.preset_list.setMaximumWidth(320)
        split.addWidget(self.preset_list)
        split.addWidget(self._build_editor())
        split.setStretchFactor(1, 1)
        split.setSizes([230, 600])
        self.stack.addWidget(split)
        layout.addWidget(self.stack, 1)

        self._rows: List[ModuleRow] = []
        self._empty_rows_key = None
        self._filled_for: Optional[int] = None

    def _build_editor(self) -> QWidget:
        self.editor_stack = QStackedWidget()
        loading = secondary(QLabel("Loading preset…"))
        loading.setAlignment(Qt.AlignCenter)
        self.editor_stack.addWidget(loading)

        editor = QWidget()
        layout = QVBoxLayout(editor)
        self.title = QLabel()
        font = self.title.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 3)
        self.title.setFont(font)
        layout.addWidget(self.title)

        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet("color: orange;")
        layout.addWidget(self.notice)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        self.rows_layout = QVBoxLayout(container)
        self.rows_layout.setSpacing(2)
        self.empty_holder = QWidget()
        self.empty_layout = QVBoxLayout(self.empty_holder)
        self.empty_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.addWidget(self.empty_holder)
        self.rows_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        # Save bar
        save = QHBoxLayout()
        save_label = QLabel("Save edits as")
        save_label.setToolTip("Edits play on the pedal right away; Save stores them in a preset slot")
        save.addWidget(save_label)
        self.save_name = QLineEdit()
        self.save_name.setPlaceholderText("Name")
        self.save_name.setMaxLength(NAME_LENGTH)
        self.save_name.setMaximumWidth(200)
        self.save_name.textChanged.connect(lambda text: self.save_button.setEnabled(bool(text)))
        save.addWidget(self.save_name)
        save.addWidget(QLabel("to"))
        self.save_target = QComboBox()
        self.save_target.setMaximumWidth(220)
        for index in range(1, 81):
            self.save_target.addItem(slot_label(index), index)
        save.addWidget(self.save_target)
        self.save_button = QPushButton("Save")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(
            lambda: self.device.save_preset(self.save_target.currentData(), self.save_name.text()))
        save.addWidget(self.save_button)
        save.addStretch()
        layout.addLayout(save)

        self.editor_stack.addWidget(editor)
        return self.editor_stack

    def _preset_clicked(self, row: int) -> None:
        index = row + 1
        if row >= 0 and index != self.device.displayed_preset_index:
            self.device.select_preset(index)

    def _copy_raw_dump(self) -> None:
        """One line per module: "0D FX 04 | 4 Cab 56 SV810 U47 | 1 55 50 12 34"."""
        lines = []
        for index in sorted(self.device.preset_cache):
            preset = self.device.preset_cache[index]
            label = f"{slot_label(index)} {preset.name}"
            for module in preset.modules:
                if module.is_empty:
                    continue
                model = catalog.model_label(module.type, module.model or 0)
                words = " ".join(map(str, module.words))
                lines.append(f"{label} | {module.slot} {module.type_name} {model} | {words}")
        QGuiApplication.clipboard().setText("\n".join(lines))

    def refresh(self) -> None:
        device = self.device
        self._set_label(self.firmware, f"Firmware {device.firmware}" if device.firmware else None)
        self._set_label(self.battery, f"Battery {device.battery}%" if device.battery is not None else None)
        self._set_label(self.tempo, f"Tempo {device.tempo} BPM" if device.tempo is not None else None)
        self._set_label(self.serial, device.serial)
        if device.link:
            symbol = "\U0001F50C" if device.link == LINK_USB else "\U0001F4F6"
            self._set_label(self.link, f"{symbol} via {device.link}")
            self.link.setToolTip("Controlling the pedal over USB (preferred while plugged in)"
                                 if device.link == LINK_USB else "Controlling the pedal over Bluetooth")
        else:
            self._set_label(self.link, None)
        cached = len(device.preset_cache)
        self._set_label(self.scanning, f"Scanning presets {cached}/80" if device.is_ready and cached < 80 else None)
        self.busy.setVisible(device.is_busy)
        self.copy_button.setEnabled(bool(device.preset_cache))

        if not device.is_ready:
            self.message.setText(f"Syncing with the pedal over {device.link}…" if device.link else
                                 "Plug the pedal in by USB, or connect to “M2Prime M2” in the device list.")
            self.stack.setCurrentIndex(0)
            self._filled_for = None
            return
        self.stack.setCurrentIndex(1)
        self._refresh_preset_list()
        self._refresh_save_targets()
        self._refresh_editor()

    @staticmethod
    def _set_label(label: QLabel, text: Optional[str]) -> None:
        label.setVisible(text is not None)
        if text is not None and label.text() != text:
            label.setText(text)

    def _refresh_preset_list(self) -> None:
        device = self.device
        displayed = device.displayed_preset_index
        for index in range(1, 81):
            item = self.preset_list.item(index - 1)
            text = f"{slot_label(index):<4} {device.preset_names.get(index, EM_DASH)}"
            if item.text() != text:
                item.setText(text)
            # Orange "?" when a preset uses a model we have no name for yet.
            preset = device.preset_cache.get(index)
            unnamed = [m for m in preset.modules if catalog.is_unnamed(m)] if preset else []
            item.setIcon(self._unnamed_icon if unnamed else self._blank_icon)
            item.setToolTip("Unnamed: " + ", ".join(
                f"{m.type_name} {catalog.model_label(m.type, m.model or 0)}" for m in unnamed) if unnamed else "")
            font = item.font()
            if font.bold() != (index == displayed):
                font.setBold(index == displayed)
                item.setFont(font)
        row = displayed - 1 if displayed else -1
        if self.preset_list.currentRow() != row:
            self.preset_list.blockSignals(True)
            self.preset_list.setCurrentRow(row)
            self.preset_list.blockSignals(False)

    def _refresh_save_targets(self) -> None:
        for index in range(1, 81):
            text = f"{slot_label(index)}  {self.device.preset_names.get(index, '')}"
            if self.save_target.itemText(index - 1) != text:
                self.save_target.setItemText(index - 1, text)

    def _refresh_editor(self) -> None:
        device = self.device
        preset = device.preset
        if preset is None:
            self.editor_stack.setCurrentIndex(0)
            self._filled_for = None
            return
        self.editor_stack.setCurrentIndex(1)
        # Fill the save form when a (different) preset loads.
        if self._filled_for != preset.index:
            self._filled_for = preset.index
            self.save_name.setText(preset.name[:NAME_LENGTH])
            self.save_target.setCurrentIndex(preset.index - 1)

        self.title.setText(f"{slot_label(preset.index)}  {preset.name}")
        self.notice.setText(f"⚠ {device.notice}" if device.notice else "")
        self.notice.setVisible(bool(device.notice))

        # Used slots in chain order, then empty ones up to 9.
        chain = [m for m in preset.modules if not m.is_empty]
        for position, module in enumerate(chain):
            if position < len(self._rows) and self._rows[position].structure == _structure(module):
                self._rows[position].set_module(module)
                continue
            row = ModuleRow(
                module,
                on_change=device.update_module,
                on_place=lambda kind, model, slot=module.slot: device.place_module(slot, kind, model),
                on_remove=lambda slot=module.slot: device.delete_module(slot))
            if position < len(self._rows):
                old = self._rows[position]
                self.rows_layout.replaceWidget(old, row)
                old.deleteLater()
                self._rows[position] = row
            else:
                self.rows_layout.insertWidget(position, row)
                self._rows.append(row)
        for old in self._rows[len(chain):]:
            self.rows_layout.removeWidget(old)
            old.deleteLater()
        del self._rows[len(chain):]

        if self._empty_rows_key != len(chain):
            self._empty_rows_key = len(chain)
            while self.empty_layout.count():
                widget = self.empty_layout.takeAt(0).widget()
                if widget:
                    widget.deleteLater()
            next_free = len(chain) + 1

            def add(kind: int) -> None:
                models = catalog.models_of(kind)
                device.place_module(next_free, kind, models[0] if models else 0)

            for position in range(len(chain), catalog.MAX_MODULES):
                self.empty_layout.addWidget(EmptySlotRow(position + 1, position == len(chain), add))


# ---- Window and app ---------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, bluetooth: BluetoothManager, device: PrimeM2Device, usb: PrimeUSBDevice) -> None:
        super().__init__()
        self.bluetooth, self.device, self.usb = bluetooth, device, usb
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(900, 600)
        split = QSplitter(Qt.Horizontal)
        self.devices = DeviceListPanel(bluetooth)
        self.chain = EffectsChainPanel(device)
        split.addWidget(self.devices)
        split.addWidget(self.chain)
        split.setStretchFactor(1, 1)
        split.setSizes([280, 900])
        self.setCentralWidget(split)

        self._refresh_pending = False
        device.listeners.append(self.schedule_refresh)
        bluetooth.listeners.append(self.schedule_refresh)
        self.refresh()

    def schedule_refresh(self) -> None:
        if not self._refresh_pending:
            self._refresh_pending = True
            QTimer.singleShot(0, self.refresh)

    def refresh(self) -> None:
        self._refresh_pending = False
        self.devices.refresh()
        self.chain.refresh()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.bluetooth.shutdown()
        self.usb.close()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Mooer Prime")
    scheduler = QtScheduler()
    invoker = MainThreadInvoker()

    device = PrimeM2Device(scheduler, log)
    usb = PrimeUSBDevice(scheduler, invoker.post, log)
    usb.on_frame = device.did_receive_usb

    def availability(available: bool) -> None:
        if available:
            device.usb_ready(usb.write_frame)
        else:
            device.usb_gone()

    usb.on_availability_change = availability
    if usb.is_connected:
        availability(True)
    bluetooth = BluetoothManager(device, invoker.post, log)

    window = MainWindow(bluetooth, device, usb)
    window.resize(1100, 700)
    window.show()
    return app.exec()

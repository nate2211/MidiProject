# gui.py
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, List

import numpy as np

from PyQt6.QtCore import Qt, QRectF, QTimer
from PyQt6.QtGui import QBrush, QColor, QPen, QPainter, QAction, QFont
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox, QListWidget, QFileDialog, QGroupBox,
    QFormLayout, QDoubleSpinBox, QSpinBox, QCheckBox, QMessageBox,
    QScrollArea, QSplitter, QToolButton,
    QGraphicsView, QGraphicsScene, QGraphicsRectItem, QGraphicsLineItem,
    QGraphicsTextItem, QSlider, QLineEdit, QInputDialog,
)

from registry import BLOCKS
import blocks  # registers blocks
from pipeline import Sequence, Track, BlockInstance, NoteEvent, midi_to_note_oct, note_oct_to_midi

NOTES = blocks.NOTES
SCALE_INTERVALS = blocks.SCALE_INTERVALS


def block_params_schema(block_name: str) -> Dict[str, Dict[str, Any]]:
    return BLOCKS.params_schema(block_name)

def block_kind(block_name: str) -> str:
    return BLOCKS.kind(block_name)

def default_params(block_name: str) -> Dict[str, Any]:
    return BLOCKS.default_params(block_name)

def _slider_steps_for_spec(spec: Dict[str, Any], *, is_int: bool) -> Tuple[float, float, float, int]:
    mn = float(spec.get("min", 0.0))
    mx = float(spec.get("max", 1.0))
    if mx <= mn:
        mx = mn + 1.0

    if is_int:
        step = float(spec.get("step", 1.0))
        step = max(1.0, round(step))
        steps_int = int(round((mx - mn) / step))
        steps_int = max(1, steps_int)
        return mn, mx, step, steps_int

    step = float(spec.get("step", 0.01))
    if step <= 0:
        step = 0.01
    steps_int = int(round((mx - mn) / step))
    steps_int = max(1, min(20000, steps_int))
    step = (mx - mn) / float(steps_int)
    return mn, mx, step, steps_int

def _value_to_slider(v: float, mn: float, step: float, steps_int: int) -> int:
    i = int(round((float(v) - mn) / step))
    return int(max(0, min(steps_int, i)))

def _slider_to_value(i: int, mn: float, step: float) -> float:
    return float(mn + float(i) * step)

def _safe_text_font(size_pt: int) -> QFont:
    size_pt = max(7, int(size_pt))
    f = QFont()
    f.setPointSize(size_pt)
    return f


@dataclass
class PianoLayout:
    left_label_w: float = 58.0
    top_header_h: float = 20.0
    cell_w: float = 26.0
    row_h: float = 18.0


class NoteItem(QGraphicsRectItem):
    def __init__(self, r: QRectF, radius: float = 3.5):
        super().__init__(r)
        self.radius = radius
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setZValue(20)

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(Qt.PenStyle.NoPen))
        painter.setBrush(self.brush())
        painter.drawRoundedRect(self.rect(), self.radius, self.radius)


class PianoRollView(QGraphicsView):
    def __init__(self, owner: "MidiCreatorGUI"):
        super().__init__()
        self.owner = owner
        self.layout = PianoLayout()

        self.setScene(QGraphicsScene(self))
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing |
            QPainter.RenderHint.TextAntialiasing |
            QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

        self._grid_items: list[Any] = []
        self._ghost_rects: list[QGraphicsRectItem] = []
        self._label_items: list[Any] = []
        self._note_items: list[NoteItem] = []
        self._note_item_by_index: dict[int, NoteItem] = {}

        self.playhead_line = QGraphicsLineItem()
        self.playhead_line.setZValue(100)
        self.playhead_line.setPen(QPen(QColor(255, 90, 90), 2.0))
        self.scene().addItem(self.playhead_line)

        self.setBackgroundBrush(QBrush(QColor(18, 18, 18)))

    def _cols(self) -> int:
        return int(self.owner.seq.total_steps())

    def _rows(self) -> int:
        return int(self.owner._piano_rows)

    def _grid_origin_x(self) -> float:
        return self.layout.left_label_w

    def _grid_origin_y(self) -> float:
        return self.layout.top_header_h

    def _scene_w(self) -> float:
        return self.layout.left_label_w + float(self._cols()) * self.layout.cell_w

    def _scene_h(self) -> float:
        return self.layout.top_header_h + float(self._rows()) * self.layout.row_h

    def _scene_x_from_step(self, step: float) -> float:
        return self._grid_origin_x() + float(step) * self.layout.cell_w

    def _scene_y_from_row(self, row: int) -> float:
        return self._grid_origin_y() + float(row) * self.layout.row_h

    def _pitch_from_row(self, row: int) -> Tuple[str, int]:
        midi = self.owner.piano_high_midi - row
        return midi_to_note_oct(midi)

    def _row_from_pitch(self, pitch: Tuple[str, int]) -> Optional[int]:
        note, octv = pitch
        midi = note_oct_to_midi(note, octv)
        if not (self.owner.piano_low_midi <= midi <= self.owner.piano_high_midi):
            return None
        return self.owner.piano_high_midi - midi

    def set_playhead_step(self, step_float: float):
        x = self._scene_x_from_step(step_float)
        x = max(self._grid_origin_x(), min(self._scene_w(), x))
        self.playhead_line.setLine(x, self._grid_origin_y(), x, self._scene_h())

    def rebuild_grid(self):
        sc = self.scene()

        for it in self._grid_items:
            sc.removeItem(it)
        for it in self._ghost_rects:
            sc.removeItem(it)
        for it in self._label_items:
            sc.removeItem(it)

        self._grid_items.clear()
        self._ghost_rects.clear()
        self._label_items.clear()

        cols = self._cols()
        rows = self._rows()

        sc.setSceneRect(0, 0, self._scene_w(), self._scene_h())

        left_bg = QGraphicsRectItem(0, 0, self.layout.left_label_w, self._scene_h())
        left_bg.setPen(QPen(Qt.PenStyle.NoPen))
        left_bg.setBrush(QBrush(QColor(12, 12, 12)))
        left_bg.setZValue(0)
        sc.addItem(left_bg)
        self._grid_items.append(left_bg)

        top_bg = QGraphicsRectItem(0, 0, self._scene_w(), self.layout.top_header_h)
        top_bg.setPen(QPen(Qt.PenStyle.NoPen))
        top_bg.setBrush(QBrush(QColor(14, 14, 14)))
        top_bg.setZValue(0)
        sc.addItem(top_bg)
        self._grid_items.append(top_bg)

        minor = QPen(QColor(35, 35, 35), 1.0)
        major = QPen(QColor(55, 55, 55), 1.0)
        hpen  = QPen(QColor(30, 30, 30), 1.0)
        bar_every = int(getattr(self.owner.seq, "steps_per_bar", 16) or 16)

        for c in range(cols + 1):
            x = self._grid_origin_x() + c * self.layout.cell_w
            pen = major if (c % bar_every == 0) else minor
            li = QGraphicsLineItem(x, self._grid_origin_y(), x, self._scene_h())
            li.setPen(pen)
            li.setZValue(1)
            sc.addItem(li)
            self._grid_items.append(li)

        for r in range(rows + 1):
            y = self._grid_origin_y() + r * self.layout.row_h
            li = QGraphicsLineItem(self._grid_origin_x(), y, self._scene_w(), y)
            li.setPen(hpen)
            li.setZValue(1)
            sc.addItem(li)
            self._grid_items.append(li)

        label_font = _safe_text_font(8)
        for r in range(rows):
            pitch = self._pitch_from_row(r)
            name = f"{pitch[0]}{pitch[1]}"
            y = self._scene_y_from_row(r)

            is_black = "#" in pitch[0]
            key_rect = QGraphicsRectItem(0, y, self.layout.left_label_w, self.layout.row_h)
            key_rect.setPen(QPen(QColor(30, 30, 30), 1.0))
            key_rect.setBrush(QBrush(QColor(20, 20, 20) if is_black else QColor(26, 26, 26)))
            key_rect.setZValue(2)
            sc.addItem(key_rect)
            self._grid_items.append(key_rect)

            t = QGraphicsTextItem(name)
            t.setDefaultTextColor(QColor(210, 210, 210))
            t.setFont(label_font)
            t.setPos(6, y + 1)
            t.setZValue(3)
            sc.addItem(t)
            self._label_items.append(t)

        header_font = _safe_text_font(8)
        for c in range(0, cols, bar_every):
            x = self._grid_origin_x() + c * self.layout.cell_w
            bar_idx = (c // bar_every) + 1
            t = QGraphicsTextItem(f"Bar {bar_idx}")
            t.setDefaultTextColor(QColor(160, 160, 160))
            t.setFont(header_font)
            t.setPos(x + 4, 2)
            t.setZValue(3)
            sc.addItem(t)
            self._label_items.append(t)

        self.set_playhead_step(self.owner._start_step)

    def rebuild_notes(self, notes: List[NoteEvent]):
        sc = self.scene()
        for it in self._note_items:
            sc.removeItem(it)
        self._note_items.clear()
        self._note_item_by_index.clear()

        for idx, ev in enumerate(notes):
            row = self._row_from_pitch(ev.pitch)
            if row is None:
                continue
            x = self._scene_x_from_step(float(ev.start_step))
            y = self._scene_y_from_row(row)
            w = float(max(1, ev.length_steps)) * self.layout.cell_w
            h = self.layout.row_h

            ni = NoteItem(QRectF(x + 1, y + 1, w - 2, h - 2))
            ni.setBrush(QBrush(QColor(60, 150, 255)))
            ni.setOpacity(0.95)
            sc.addItem(ni)
            self._note_items.append(ni)
            self._note_item_by_index[idx] = ni

        self.set_playhead_step(self.owner._start_step)

    def wheelEvent(self, ev):
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = ev.angleDelta().y()
            if delta > 0:
                self.scale(1.12, 1.0)
            else:
                self.scale(1 / 1.12, 1.0)
            ev.accept()
            return
        super().wheelEvent(ev)

    def mousePressEvent(self, ev):
        # keep Alt+Click playhead behavior (useful even for generation workflow)
        if ev.button() == Qt.MouseButton.LeftButton and (ev.modifiers() & Qt.KeyboardModifier.AltModifier):
            sp = self.mapToScene(ev.pos())
            if sp.x() >= self._grid_origin_x() and sp.y() >= self._grid_origin_y():
                step = int((sp.x() - self._grid_origin_x()) // self.layout.cell_w)
                step = max(0, min(self._cols() - 1, step))
                self.owner._set_start_step(step)
                ev.accept()
                return
        super().mousePressEvent(ev)


class MidiCreatorGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("midicreator - Blocks → Dynamic MIDI Generation (FL Studio)")

        self.seq = Sequence(bpm=120.0, steps_per_bar=16, bars=2, ticks_per_beat=480)
        self.seq.tracks = [
            Track(
                name="Track 1",
                generators=[BlockInstance("random_melody", default_params("random_melody"))],
                fx=[BlockInstance("scale_lock", default_params("scale_lock"))],
                instruments=[BlockInstance("midi_program", default_params("midi_program"))],
            ),
        ]
        # default channels (FL Studio-friendly)
        self.seq.tracks[0].instruments[0].params["channel"] = 0
        self.seq.ensure()

        self.piano_low_midi = 12
        self.piano_high_midi = 127
        self._piano_rows = (self.piano_high_midi - self.piano_low_midi + 1)

        self._start_step = 0
        self._editing: Optional[Tuple[int, str, int]] = None  # (track_i, "generators"/"fx"/"instruments", idx)

        self._auto_generate = True
        self._regen_timer = QTimer(self)
        self._regen_timer.setSingleShot(True)
        self._regen_timer.setInterval(120)
        self._regen_timer.timeout.connect(self.generate_now)

        self.init_ui()
        self._apply_dark_theme()
        self.generate_now()

    def init_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        main = QHBoxLayout(root)
        main.setContentsMargins(10, 10, 10, 10)
        main.setSpacing(10)

        lr_split = QSplitter(Qt.Orientation.Horizontal)
        main.addWidget(lr_split)

        # LEFT
        left_pane = QWidget()
        left_layout = QVBoxLayout(left_pane)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        top_box = QGroupBox("Generate")
        top_l = QHBoxLayout(top_box)

        self.bpm_spin = QSpinBox()
        self.bpm_spin.setRange(20, 300)
        self.bpm_spin.setValue(int(self.seq.bpm))
        self.bpm_spin.valueChanged.connect(self.on_bpm_changed)

        self.bars_spin = QSpinBox()
        self.bars_spin.setRange(1, 128)
        self.bars_spin.setValue(int(self.seq.bars))
        self.bars_spin.valueChanged.connect(self.on_bars_changed)

        self.auto_box = QCheckBox("Auto-generate")
        self.auto_box.setChecked(True)
        self.auto_box.stateChanged.connect(lambda _: self._set_auto(self.auto_box.isChecked()))

        self.btn_generate = QPushButton("Generate Now")
        self.btn_generate.clicked.connect(self.generate_now)

        self.btn_export = QPushButton("Export MIDI")
        self.btn_export.clicked.connect(self.on_export_midi)

        self.btn_export_track = QPushButton("Export Track")
        self.btn_export_track.clicked.connect(self.on_export_track)

        top_l.addWidget(QLabel("BPM"))
        top_l.addWidget(self.bpm_spin)
        top_l.addSpacing(10)
        top_l.addWidget(QLabel("Bars"))
        top_l.addWidget(self.bars_spin)
        top_l.addSpacing(10)
        top_l.addWidget(self.auto_box)
        top_l.addStretch(1)
        top_l.addWidget(QLabel("Alt+Click sets playhead"))
        top_l.addWidget(self.btn_generate)
        top_l.addWidget(self.btn_export)
        top_l.addWidget(self.btn_export_track)

        left_layout.addWidget(top_box)

        roll_box = QGroupBox("Preview (Generated Notes)")
        roll_l = QVBoxLayout(roll_box)
        self.roll = PianoRollView(self)
        roll_l.addWidget(self.roll, 1)
        left_layout.addWidget(roll_box, 1)

        # RIGHT
        right_pane = QWidget()
        right_layout = QVBoxLayout(right_pane)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        right_split = QSplitter(Qt.Orientation.Vertical)
        right_layout.addWidget(right_split, 1)

        # Tracks
        tracks_box = QGroupBox("Tracks")
        tracks_l = QVBoxLayout(tracks_box)
        tracks_l.setContentsMargins(10, 10, 10, 10)
        track_btns = QHBoxLayout()
        self.btn_add_track = QPushButton("Add Track")
        self.btn_del_track = QPushButton("Delete Track")
        track_btns.addWidget(self.btn_add_track)
        track_btns.addWidget(self.btn_del_track)
        tracks_l.addLayout(track_btns)

        self.btn_add_track.clicked.connect(self.add_track)
        self.btn_del_track.clicked.connect(self.delete_track)
        self.track_list = QListWidget()
        self.track_list.itemDoubleClicked.connect(self.rename_track)
        self.track_list.currentRowChanged.connect(self.on_track_selected)
        tracks_l.addWidget(self.track_list, 1)
        right_split.addWidget(tracks_box)

        # Generators
        gen_box = QGroupBox("Generators (create notes)")
        gen_l = QVBoxLayout(gen_box)
        gen_l.setContentsMargins(10, 10, 10, 10)

        self.gen_picker = QComboBox()
        for n in BLOCKS.names():
            if block_kind(n) == "generator":
                self.gen_picker.addItem(n)
        gen_l.addWidget(self.gen_picker)

        gen_btns = QHBoxLayout()
        self.btn_add_gen = QPushButton("Add")
        self.btn_rm_gen = QPushButton("Remove")
        gen_btns.addWidget(self.btn_add_gen)
        gen_btns.addWidget(self.btn_rm_gen)
        gen_l.addLayout(gen_btns)

        self.gen_list = QListWidget()
        gen_l.addWidget(self.gen_list, 1)

        self.btn_add_gen.clicked.connect(self.add_generator)
        self.btn_rm_gen.clicked.connect(self.remove_generator)
        self.gen_list.currentRowChanged.connect(lambda _: self.select_block_for_edit("generators"))

        right_split.addWidget(gen_box)

        # FX
        fx_box = QGroupBox("FX (transform notes)")
        fx_l = QVBoxLayout(fx_box)
        fx_l.setContentsMargins(10, 10, 10, 10)

        self.fx_picker = QComboBox()
        for n in BLOCKS.names():
            if block_kind(n) == "fx":
                self.fx_picker.addItem(n)
        fx_l.addWidget(self.fx_picker)

        fx_btns = QHBoxLayout()
        self.btn_add_fx = QPushButton("Add")
        self.btn_rm_fx = QPushButton("Remove")
        fx_btns.addWidget(self.btn_add_fx)
        fx_btns.addWidget(self.btn_rm_fx)
        fx_l.addLayout(fx_btns)

        self.fx_list = QListWidget()
        fx_l.addWidget(self.fx_list, 1)

        self.btn_add_fx.clicked.connect(self.add_fx)
        self.btn_rm_fx.clicked.connect(self.remove_fx)
        self.fx_list.currentRowChanged.connect(lambda _: self.select_block_for_edit("fx"))

        right_split.addWidget(fx_box)

        # Instruments
        inst_box = QGroupBox("Instruments/Settings")
        inst_l = QVBoxLayout(inst_box)
        inst_l.setContentsMargins(10, 10, 10, 10)

        self.inst_picker = QComboBox()
        for n in BLOCKS.names():
            if block_kind(n) == "instrument":
                self.inst_picker.addItem(n)
        inst_l.addWidget(self.inst_picker)

        inst_btns = QHBoxLayout()
        self.btn_add_inst = QPushButton("Add")
        self.btn_rm_inst = QPushButton("Remove")
        inst_btns.addWidget(self.btn_add_inst)
        inst_btns.addWidget(self.btn_rm_inst)
        inst_l.addLayout(inst_btns)

        self.inst_list = QListWidget()
        inst_l.addWidget(self.inst_list, 1)

        self.btn_add_inst.clicked.connect(self.add_instrument)
        self.btn_rm_inst.clicked.connect(self.remove_instrument)
        self.inst_list.currentRowChanged.connect(lambda _: self.select_block_for_edit("instruments"))

        right_split.addWidget(inst_box)

        # Params
        params_box = QGroupBox("Block Params")
        pv = QVBoxLayout(params_box)
        pv.setContentsMargins(10, 10, 10, 10)
        self.params_scroll = QScrollArea()
        self.params_scroll.setWidgetResizable(True)
        pv.addWidget(self.params_scroll, 1)
        self.params_inner = QWidget()
        self.params_form = QFormLayout(self.params_inner)
        self.params_scroll.setWidget(self.params_inner)
        right_split.addWidget(params_box)

        right_split.setStretchFactor(0, 0)  # tracks
        right_split.setStretchFactor(1, 1)  # generators
        right_split.setStretchFactor(2, 1)  # fx
        right_split.setStretchFactor(3, 0)  # instruments
        right_split.setStretchFactor(4, 2)  # params
        right_split.setSizes([140, 220, 220, 180, 360])

        lr_split.addWidget(left_pane)
        lr_split.addWidget(right_pane)
        lr_split.setStretchFactor(0, 3)
        lr_split.setStretchFactor(1, 1)

        # init
        self.refresh_tracks()
        self.track_list.setCurrentRow(0)

        act = QAction(self)
        act.setShortcut("Space")
        act.triggered.connect(self.generate_now)
        self.addAction(act)

    def _apply_dark_theme(self):
        self.setStyleSheet("""
        QWidget { background: #151515; color: #e8e8e8; }
        QGroupBox {
            border: 1px solid #2a2a2a;
            border-radius: 8px;
            margin-top: 10px;
            padding: 8px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 6px 0 6px;
            color: #cfcfcf;
        }
        QPushButton {
            background: #262626;
            border: 1px solid #353535;
            border-radius: 8px;
            padding: 6px 10px;
        }
        QPushButton:hover { background: #2d2d2d; }
        QListWidget {
            background: #191919;
            border: 1px solid #2f2f2f;
            border-radius: 8px;
        }
        QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {
            background: #1d1d1d;
            border: 1px solid #333333;
            border-radius: 6px;
            padding: 4px;
        }
        QScrollArea { border: none; }
        """)
        self.roll.setStyleSheet("background: #111111; border: 1px solid #2f2f2f; border-radius: 10px;")

    # ---------------- helpers ----------------

    def on_export_track(self):
        ti = self.current_track_index()
        if ti < 0:
            QMessageBox.information(self, "No track selected", "Select a track first.")
            return

        track_name = self.seq.tracks[ti].name if (0 <= ti < len(self.seq.tracks)) else f"track_{ti + 1}"
        safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in track_name).strip("_")
        default_name = f"{safe_name or 'track'}_{ti + 1}.mid"

        path, _ = QFileDialog.getSaveFileName(self, "Export Track MIDI", default_name, "MIDI Files (*.mid)")
        if not path:
            return

        try:
            # --- create a temporary Sequence that contains ONLY this track ---
            one = Sequence(
                bpm=float(self.seq.bpm),
                steps_per_bar=int(self.seq.steps_per_bar),
                bars=int(self.seq.bars),
                ticks_per_beat=int(self.seq.ticks_per_beat),
            )
            # keep only the selected track (shallow copy is fine; we won't mutate blocks here)
            one.tracks = [self.seq.tracks[ti]]
            one.ensure()

            # write MIDI for just that single-track sequence
            one.write_midi(path)

            QMessageBox.information(
                self,
                "Export Successful",
                f"Track exported to:\n{path}\n\nImport it in FL Studio (File → Import → MIDI)."
            )
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))
    def rename_track(self, item):
        ti = self.current_track_index()
        if ti < 0:
            return
        name, ok = QInputDialog.getText(self, "Rename Track", "Track name:", text=item.text())
        if ok and name.strip():
            self.seq.tracks[ti].name = name.strip()
            item.setText(name.strip())
    def current_track_index(self) -> int:
        return self.track_list.currentRow()

    def refresh_tracks(self):
        self.track_list.clear()
        for t in self.seq.tracks:
            self.track_list.addItem(t.name)

    def refresh_stacks(self, ti: int):
        tr = self.seq.tracks[ti]
        self.gen_list.clear()
        for bi in tr.generators:
            self.gen_list.addItem(bi.name)
        self.fx_list.clear()
        for bi in tr.fx:
            self.fx_list.addItem(bi.name)
        self.inst_list.clear()
        for bi in tr.instruments:
            self.inst_list.addItem(bi.name)

    def _set_start_step(self, step: int):
        step = int(max(0, min(self.seq.total_steps() - 1, step)))
        self._start_step = step
        self.roll.set_playhead_step(step)

    def _set_auto(self, on: bool):
        self._auto_generate = bool(on)

    def schedule_regen(self):
        if self._auto_generate:
            self._regen_timer.start()

    # ---------------- generation ----------------

    def generate_now(self):
        ti = self.current_track_index()
        if ti < 0:
            ti = 0
        self.seq.ensure()
        notes = self.seq.preview_track_notes(ti, include_manual=False)
        self.roll.rebuild_grid()
        self.roll.rebuild_notes(notes)
        self.roll.set_playhead_step(self._start_step)

    # ---------------- tempo/bars ----------------

    def on_bpm_changed(self, v: int):
        self.seq.bpm = float(v)
        self.schedule_regen()

    def on_bars_changed(self, bars: int):
        self.seq.bars = int(max(1, int(bars)))
        self.seq.ensure()
        if self._start_step >= self.seq.total_steps():
            self._start_step = max(0, self.seq.total_steps() - 1)
        self.schedule_regen()
        self.generate_now()

    # ---------------- selection ----------------
    def add_track(self):
        # create a new track with sensible defaults
        idx = len(self.seq.tracks) + 1
        t = Track(
            name=f"Track {idx}",
            generators=[BlockInstance("random_melody", default_params("random_melody"))],
            fx=[BlockInstance("scale_lock", default_params("scale_lock"))],
            instruments=[BlockInstance("midi_program", default_params("midi_program"))],
        )
        # choose a channel that won’t collide too much (0-15)
        try:
            ch = (idx - 1) % 16
            t.instruments[0].params["channel"] = int(ch)
        except Exception:
            pass

        self.seq.tracks.append(t)
        self.seq.ensure()

        self.refresh_tracks()
        self.track_list.setCurrentRow(len(self.seq.tracks) - 1)  # select new track
        self.refresh_stacks(self.current_track_index())
        self.clear_param_editor()
        self.generate_now()

    def delete_track(self):
        ti = self.current_track_index()
        if ti < 0:
            return

        # never allow deleting the last remaining track
        if len(self.seq.tracks) <= 1:
            QMessageBox.information(self, "Can't delete", "You must keep at least one track.")
            return

        self.seq.tracks.pop(ti)

        # IMPORTANT: keep seq.notes aligned with tracks if your pipeline stores per-track notes
        # Many implementations keep seq.notes as list-of-lists same length as tracks.
        # If yours does, do this:
        if hasattr(self.seq, "notes") and isinstance(self.seq.notes, list):
            if len(self.seq.notes) > ti:
                self.seq.notes.pop(ti)

        self.seq.ensure()

        # select a valid track index after deletion
        new_idx = max(0, min(ti, len(self.seq.tracks) - 1))
        self.refresh_tracks()
        self.track_list.setCurrentRow(new_idx)
        self.refresh_stacks(new_idx)
        self.clear_param_editor()
        self.generate_now()
    def on_track_selected(self, idx: int):
        if idx < 0:
            return
        self.refresh_stacks(idx)
        self.clear_param_editor()
        self.generate_now()

    # ---------------- stacks modify ----------------

    def add_generator(self):
        ti = self.current_track_index()
        if ti < 0:
            return
        name = self.gen_picker.currentText().strip().lower()
        self.seq.tracks[ti].generators.append(BlockInstance(name, default_params(name)))
        self.refresh_stacks(ti)
        self.schedule_regen()
        self.generate_now()

    def remove_generator(self):
        ti = self.current_track_index()
        if ti < 0:
            return
        idx = self.gen_list.currentRow()
        if idx < 0:
            return
        tr = self.seq.tracks[ti]
        if 0 <= idx < len(tr.generators):
            tr.generators.pop(idx)
        self.refresh_stacks(ti)
        self.clear_param_editor()
        self.schedule_regen()
        self.generate_now()

    def add_fx(self):
        ti = self.current_track_index()
        if ti < 0:
            return
        name = self.fx_picker.currentText().strip().lower()
        self.seq.tracks[ti].fx.append(BlockInstance(name, default_params(name)))
        self.refresh_stacks(ti)
        self.schedule_regen()
        self.generate_now()

    def remove_fx(self):
        ti = self.current_track_index()
        if ti < 0:
            return
        idx = self.fx_list.currentRow()
        if idx < 0:
            return
        tr = self.seq.tracks[ti]
        if 0 <= idx < len(tr.fx):
            tr.fx.pop(idx)
        self.refresh_stacks(ti)
        self.clear_param_editor()
        self.schedule_regen()
        self.generate_now()

    def add_instrument(self):
        ti = self.current_track_index()
        if ti < 0:
            return
        name = self.inst_picker.currentText().strip().lower()
        self.seq.tracks[ti].instruments.append(BlockInstance(name, default_params(name)))
        self.refresh_stacks(ti)
        self.schedule_regen()
        self.generate_now()

    def remove_instrument(self):
        ti = self.current_track_index()
        if ti < 0:
            return
        idx = self.inst_list.currentRow()
        if idx < 0:
            return
        tr = self.seq.tracks[ti]
        if 0 <= idx < len(tr.instruments):
            tr.instruments.pop(idx)
        self.refresh_stacks(ti)
        self.clear_param_editor()
        self.schedule_regen()
        self.generate_now()

    # ---------------- param editor ----------------

    def _clear_param_rows(self):
        while self.params_form.rowCount():
            self.params_form.removeRow(0)

    def clear_param_editor(self):
        self._clear_param_rows()
        self._editing = None

    def select_block_for_edit(self, stack_name: str):
        ti = self.current_track_index()
        if ti < 0:
            self.clear_param_editor()
            return

        tr = self.seq.tracks[ti]
        idx = -1
        blk = None

        if stack_name == "generators":
            idx = self.gen_list.currentRow()
            if 0 <= idx < len(tr.generators):
                blk = tr.generators[idx]
        elif stack_name == "fx":
            idx = self.fx_list.currentRow()
            if 0 <= idx < len(tr.fx):
                blk = tr.fx[idx]
        elif stack_name == "instruments":
            idx = self.inst_list.currentRow()
            if 0 <= idx < len(tr.instruments):
                blk = tr.instruments[idx]

        if blk is None:
            self.clear_param_editor()
            return

        self._editing = (ti, stack_name, idx)
        self.build_param_editor(blk.name, blk.params)

    def build_param_editor(self, block_name: str, params: Dict[str, Any]):
        self._clear_param_rows()
        schema = block_params_schema(block_name)
        if not schema:
            self.params_form.addRow(QLabel("(No params)"), QLabel(""))
            return

        for key, spec in schema.items():
            params.setdefault(key, spec.get("default"))

        for key, spec in schema.items():
            ptype = spec.get("type", "float")
            default = spec.get("default")

            if ptype == "bool":
                w = QCheckBox()
                w.setChecked(bool(params.get(key)))
                w.stateChanged.connect(lambda _=0, k=key, wid=w: self._set_param(k, wid.isChecked()))
                self.params_form.addRow(QLabel(key), w)
                continue

            if ptype == "str":
                w = QLineEdit()
                w.setText(str(params.get(key, default) or ""))
                w.textChanged.connect(lambda val, k=key: self._set_param(k, str(val)))
                self.params_form.addRow(QLabel(key), w)
                continue

            if ptype == "choice":
                w = QComboBox()
                choices = list(spec.get("choices", []))
                for c in choices:
                    w.addItem(str(c))
                cur = str(params.get(key, default))
                if cur in [str(x) for x in choices]:
                    w.setCurrentText(cur)
                w.currentTextChanged.connect(lambda val, k=key: self._set_param(k, val))
                self.params_form.addRow(QLabel(key), w)
                continue

            is_int = (ptype == "int")
            mn, mx, step, steps_int = _slider_steps_for_spec(spec, is_int=is_int)

            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(8)

            sld = QSlider(Qt.Orientation.Horizontal)
            sld.setMinimum(0)
            sld.setMaximum(steps_int)
            sld.setSingleStep(1)
            sld.setPageStep(max(1, steps_int // 20))

            if is_int:
                spn = QSpinBox()
                spn.setMinimum(int(round(mn)))
                spn.setMaximum(int(round(mx)))
                spn.setSingleStep(max(1, int(round(spec.get("step", 1)))))
                spn.setFixedWidth(90)
            else:
                spn = QDoubleSpinBox()
                spn.setDecimals(int(spec.get("decimals", 6)))
                spn.setMinimum(float(mn))
                spn.setMaximum(float(mx))
                spn.setSingleStep(float(spec.get("step", 0.01)))
                spn.setFixedWidth(110)

            cur_v = params.get(key, default)
            cur_v = float(cur_v if cur_v is not None else (mn + mx) * 0.5)
            cur_v = float(np.clip(cur_v, mn, mx))

            sld.setValue(_value_to_slider(cur_v, mn, step, steps_int))
            spn.setValue(int(round(cur_v)) if is_int else cur_v)

            def on_slider(val, k=key, slider=sld, spin=spn, mn_=mn, step_=step, is_int_=is_int):
                v = _slider_to_value(int(val), mn_, step_)
                if is_int_:
                    v = int(round(v))
                    spin.blockSignals(True); spin.setValue(v); spin.blockSignals(False)
                    self._set_param(k, v)
                else:
                    spin.blockSignals(True); spin.setValue(float(v)); spin.blockSignals(False)
                    self._set_param(k, float(v))

            def on_spin(val, k=key, slider=sld, mn_=mn, step_=step, steps_=steps_int, is_int_=is_int):
                v = float(val)
                v = float(np.clip(v, mn_, mx))
                slider.blockSignals(True); slider.setValue(_value_to_slider(v, mn_, step_, steps_)); slider.blockSignals(False)
                self._set_param(k, int(round(v)) if is_int_ else float(v))

            sld.valueChanged.connect(on_slider)
            spn.valueChanged.connect(on_spin)

            h.addWidget(sld, 1)
            h.addWidget(spn, 0)
            self.params_form.addRow(QLabel(key), row)

    def _set_param(self, key: str, value: Any):
        if not self._editing:
            return

        ti, stack_name, bi = self._editing
        if not (0 <= ti < len(self.seq.tracks)):
            return
        tr = self.seq.tracks[ti]

        stack = None
        if stack_name == "generators":
            stack = tr.generators
        elif stack_name == "fx":
            stack = tr.fx
        elif stack_name == "instruments":
            stack = tr.instruments

        if stack is None or not (0 <= bi < len(stack)):
            return

        stack[bi].params[key] = value
        self.schedule_regen()

    # ---------------- export ----------------

    def on_export_midi(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export MIDI", "out.mid", "MIDI Files (*.mid)")
        if not path:
            return
        try:
            self.seq.write_midi(path)
            QMessageBox.information(self, "Export Successful", f"MIDI exported to:\n{path}\n\nImport it in FL Studio (File → Import → MIDI).")
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))


def main():
    app = QApplication(sys.argv)
    w = MidiCreatorGUI()
    w.resize(1500, 900)
    w.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()

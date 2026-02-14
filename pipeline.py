# pipeline.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from registry import BLOCKS
import blocks  # registers blocks

NOTES = blocks.NOTES

def midi_to_note_oct(midi: int) -> Tuple[str, int]:
    note = NOTES[midi % 12]
    octave = (midi // 12) - 1
    return note, octave

def note_oct_to_midi(note: str, octave: int) -> int:
    semi = NOTES.index(note)
    return (octave + 1) * 12 + semi

def _clamp_int(x: int, lo: int, hi: int) -> int:
    return int(max(lo, min(hi, int(x))))

@dataclass
class BlockInstance:
    name: str
    params: Dict[str, Any]

@dataclass
class NoteEvent:
    start_step: int
    length_steps: int
    pitch: Tuple[str, int]  # ("C#", 4)
    velocity: int = 90

@dataclass
class Track:
    name: str = "Track"
    generators: List[BlockInstance] = None
    fx: List[BlockInstance] = None
    instruments: List[BlockInstance] = None  # settings blocks

    def __post_init__(self):
        self.generators = self.generators or []
        self.fx = self.fx or []
        self.instruments = self.instruments or []

@dataclass
class Sequence:
    bpm: float = 120.0
    steps_per_bar: int = 16
    bars: int = 2
    ticks_per_beat: int = 480
    tracks: List[Track] = None

    # optional manual notes (if you want manual override)
    manual_notes: List[List[NoteEvent]] = None

    def ensure(self) -> None:
        if self.tracks is None:
            self.tracks = [Track(name="Track 1")]
        if self.manual_notes is None:
            self.manual_notes = [[] for _ in self.tracks]
        if len(self.manual_notes) < len(self.tracks):
            self.manual_notes.extend([[] for _ in range(len(self.tracks) - len(self.manual_notes))])
        elif len(self.manual_notes) > len(self.tracks):
            self.manual_notes = self.manual_notes[:len(self.tracks)]

        self.steps_per_bar = max(1, int(self.steps_per_bar))
        self.bars = max(1, int(self.bars))
        self.ticks_per_beat = max(24, int(self.ticks_per_beat))
        self.bpm = float(self.bpm if self.bpm > 0 else 120.0)

    def total_steps(self) -> int:
        return int(self.steps_per_bar * self.bars)

    def steps_per_beat(self) -> float:
        return float(self.steps_per_bar) / 4.0  # assume 4/4 grid

    def ticks_per_step(self) -> int:
        spb = self.steps_per_beat()
        if spb <= 0:
            spb = 4.0
        return int(round(float(self.ticks_per_beat) / spb))

    # ----------------- generation pipeline -----------------

    def build_track_settings(self, ti: int) -> Dict[str, Any]:
        tr = self.tracks[ti]
        settings = {
            "name": tr.name,
            "channel": _clamp_int(ti, 0, 15),
            "program": 0,
            "base_velocity": 90,
        }
        for bi in (tr.instruments or []):
            cls = BLOCKS.cls(bi.name)
            settings = cls.process_instrument(settings, dict(bi.params or {}))
        settings["channel"] = _clamp_int(settings.get("channel", ti), 0, 15)
        settings["program"] = _clamp_int(settings.get("program", 0), 0, 127)
        settings["base_velocity"] = _clamp_int(settings.get("base_velocity", 90), 1, 127)
        return settings

    def generate_track_notes_dicts(self, ti: int) -> List[Dict[str, Any]]:
        """
        Generator blocks create notes.
        FX blocks transform notes.
        Instruments blocks define settings (channel/program/base vel).
        """
        self.ensure()
        total = int(self.total_steps())
        tr = self.tracks[ti]
        settings = self.build_track_settings(ti)

        notes: List[Dict[str, Any]] = []

        # --- generators ---
        for bi in (tr.generators or []):
            cls = BLOCKS.cls(bi.name)
            notes = cls.process_generate(notes, self, dict(bi.params or {}), settings) or notes

        # fill defaults & clamp
        for n in notes:
            n.setdefault("velocity", settings["base_velocity"])
            n.setdefault("channel", settings["channel"])
            n["start_step"] = _clamp_int(int(n.get("start_step", 0)), 0, total - 1)
            n["length_steps"] = max(1, int(n.get("length_steps", 1)))
            n["length_steps"] = min(int(n["length_steps"]), max(1, total - int(n["start_step"])))
            n["midi"] = _clamp_int(int(n.get("midi", 60)), 0, 127)
            n["velocity"] = _clamp_int(int(n.get("velocity", settings["base_velocity"])), 1, 127)
            n["channel"] = _clamp_int(int(n.get("channel", settings["channel"])), 0, 15)

        notes.sort(key=lambda z: (int(z["start_step"]), int(z["midi"])))

        # --- fx ---
        for bi in (tr.fx or []):
            cls = BLOCKS.cls(bi.name)
            notes = cls.process_notes(notes, self, dict(bi.params or {}), settings) or notes

        # re-clamp after fx
        out: List[Dict[str, Any]] = []
        for n in notes:
            s = _clamp_int(int(n.get("start_step", 0)), 0, total - 1)
            ln = max(1, int(n.get("length_steps", 1)))
            ln = min(ln, max(1, total - s))
            out.append({
                "start_step": s,
                "length_steps": ln,
                "midi": _clamp_int(int(n.get("midi", 60)), 0, 127),
                "velocity": _clamp_int(int(n.get("velocity", settings["base_velocity"])), 1, 127),
                "channel": _clamp_int(int(n.get("channel", settings["channel"])), 0, 15),
            })
        out.sort(key=lambda z: (int(z["start_step"]), int(z["midi"])))
        return out

    def preview_track_notes(self, ti: int, *, include_manual: bool = False) -> List[NoteEvent]:
        """
        Used by GUI to display notes. Focus is generated notes.
        Manual notes are optional overlay.
        """
        self.ensure()
        gen = self.generate_track_notes_dicts(ti)
        out: List[NoteEvent] = []
        for n in gen:
            pitch = midi_to_note_oct(int(n["midi"]))
            out.append(NoteEvent(int(n["start_step"]), int(n["length_steps"]), pitch, int(n["velocity"])))

        if include_manual:
            for ev in self.manual_notes[ti]:
                out.append(ev)

        out.sort(key=lambda e: (int(e.start_step), note_oct_to_midi(e.pitch[0], e.pitch[1])))
        return out

    # ----------------- MIDI export -----------------

    def write_midi(self, path: str) -> None:
        self.ensure()
        tps = int(self.ticks_per_step())

        def u16(n: int) -> bytes:
            return bytes([(n >> 8) & 0xFF, n & 0xFF])

        def u32(n: int) -> bytes:
            return bytes([(n >> 24) & 0xFF, (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF])

        def varlen(n: int) -> bytes:
            n = int(max(0, n))
            out = [n & 0x7F]
            n >>= 7
            while n:
                out.append(0x80 | (n & 0x7F))
                n >>= 7
            out.reverse()
            return bytes(out)

        def meta_track_name(name: str) -> bytes:
            b = name.encode("utf-8", errors="replace")
            return bytes([0xFF, 0x03]) + varlen(len(b)) + b

        def meta_tempo(bpm: float) -> bytes:
            mpqn = int(round(60_000_000.0 / float(bpm if bpm > 0 else 120.0)))
            return bytes([0xFF, 0x51, 0x03, (mpqn >> 16) & 0xFF, (mpqn >> 8) & 0xFF, mpqn & 0xFF])

        def meta_time_sig(numer: int, denom: int) -> bytes:
            denom = int(denom)
            exp = 0
            d = denom
            while d > 1 and (d % 2 == 0):
                d //= 2
                exp += 1
            return bytes([0xFF, 0x58, 0x04, numer & 0xFF, exp & 0xFF, 24, 8])

        def prog_change(ch: int, program: int) -> bytes:
            return bytes([0xC0 | (ch & 0x0F), program & 0x7F])

        def note_on(ch: int, note: int, vel: int) -> bytes:
            return bytes([0x90 | (ch & 0x0F), note & 0x7F, vel & 0x7F])

        def note_off(ch: int, note: int) -> bytes:
            return bytes([0x80 | (ch & 0x0F), note & 0x7F, 0])

        def mtrk(delta_events: List[Tuple[int, bytes]]) -> bytes:
            body = bytearray()
            for dt, raw in delta_events:
                body += varlen(dt)
                body += raw
            body += varlen(0) + bytes([0xFF, 0x2F, 0x00])
            return b"MTrk" + u32(len(body)) + bytes(body)

        # Track 0 meta
        t0: List[Tuple[int, bytes]] = []
        t0.append((0, meta_track_name("midicreator")))
        t0.append((0, meta_tempo(self.bpm)))
        t0.append((0, meta_time_sig(4, 4)))
        mtrks: List[bytes] = [mtrk(t0)]

        # Note tracks
        for ti, tr in enumerate(self.tracks):
            settings = self.build_track_settings(ti)
            ch = int(settings["channel"]) & 0x0F
            program = int(settings["program"])
            trname = str(settings["name"])

            nds = self.generate_track_notes_dicts(ti)

            abs_events: List[Tuple[int, int, bytes]] = []
            abs_events.append((0, 2, meta_track_name(trname)))
            abs_events.append((0, 3, prog_change(ch, program)))

            for n in nds:
                start_tick = int(n["start_step"]) * tps
                end_tick = int(n["start_step"] + max(1, int(n["length_steps"]))) * tps
                midi = int(n["midi"])
                vel = int(n["velocity"])

                abs_events.append((end_tick, 0, note_off(ch, midi)))
                abs_events.append((start_tick, 1, note_on(ch, midi, vel)))

            abs_events.sort(key=lambda x: (x[0], x[1]))

            out: List[Tuple[int, bytes]] = []
            last = 0
            for t_abs, _, raw in abs_events:
                dt = int(t_abs - last)
                out.append((dt, raw))
                last = t_abs

            mtrks.append(mtrk(out))

        header = b"MThd" + u32(6) + u16(1) + u16(len(mtrks)) + u16(int(self.ticks_per_beat))
        with open(path, "wb") as f:
            f.write(header)
            for trk in mtrks:
                f.write(trk)

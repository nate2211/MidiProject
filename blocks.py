# blocks.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import random

from registry import BLOCKS

NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

SCALE_INTERVALS: Dict[str, list[int]] = {
    "major":               [0, 2, 4, 5, 7, 9, 11],
    "natural_minor":       [0, 2, 3, 5, 7, 8, 10],
    "harmonic_minor":      [0, 2, 3, 5, 7, 8, 11],
    "melodic_minor":       [0, 2, 3, 5, 7, 9, 11],
    "pentatonic_major":    [0, 2, 4, 7, 9],
    "pentatonic_minor":    [0, 3, 5, 7, 10],
    "blues":               [0, 3, 5, 6, 7, 10],
    "dorian":              [0, 2, 3, 5, 7, 9, 10],
    "phrygian":            [0, 1, 3, 5, 7, 8, 10],
    "lydian":              [0, 2, 4, 6, 7, 9, 11],
    "mixolydian":          [0, 2, 4, 5, 7, 9, 10],
    "locrian":             [0, 1, 3, 5, 6, 8, 10],
}

def _clamp_int(x: int, lo: int, hi: int) -> int:
    return int(max(lo, min(hi, int(x))))

def _note_to_pc(note: str) -> int:
    return NOTES.index(note)

def _scale_pcs(root: str, scale: str) -> List[int]:
    root_pc = _note_to_pc(root)
    ints = SCALE_INTERVALS.get(scale, [])
    return [((root_pc + i) % 12) for i in ints]

def _snap_to_scale(midi: int, pcs: set[int]) -> int:
    if (midi % 12) in pcs:
        return midi
    best = midi
    best_d = 999
    for d in range(1, 12):
        for cand in (midi - d, midi + d):
            if 0 <= cand <= 127 and (cand % 12) in pcs:
                if d < best_d:
                    best_d = d
                    best = cand
    return best

# ---------------- Bjorklund (Euclidean rhythm) ----------------

def _bjorklund(steps: int, pulses: int) -> List[int]:
    steps = int(max(1, steps))
    pulses = int(max(0, min(steps, pulses)))
    if pulses == 0:
        return [0] * steps
    if pulses == steps:
        return [1] * steps

    pattern = []
    counts = []
    remainders = []
    divisor = steps - pulses
    remainders.append(pulses)
    level = 0

    while True:
        counts.append(divisor // remainders[level])
        remainders.append(divisor % remainders[level])
        divisor = remainders[level]
        level += 1
        if remainders[level] <= 1:
            break
    counts.append(divisor)

    def build(lvl: int):
        if lvl == -1:
            pattern.append(0)
        elif lvl == -2:
            pattern.append(1)
        else:
            for _ in range(counts[lvl]):
                build(lvl - 1)
            if remainders[lvl] != 0:
                build(lvl - 2)

    build(level)
    # rotate so it starts with a hit
    while pattern and pattern[0] == 0:
        pattern = pattern[1:] + pattern[:1]
    return pattern[:steps]


def _weighted_choice(rng: random.Random, items: List[Tuple[Any, float]]):
    total = 0.0
    for _, w in items:
        total += max(0.0, float(w))
    if total <= 0:
        return items[0][0]
    r = rng.random() * total
    acc = 0.0
    for v, w in items:
        acc += max(0.0, float(w))
        if r <= acc:
            return v
    return items[-1][0]

def _nearest_in_pool(m: int, pool_sorted: List[int]) -> int:
    # pool_sorted must be sorted ascending
    if not pool_sorted:
        return _clamp_int(m, 0, 127)
    # binary-ish linear is fine at these sizes
    best = pool_sorted[0]
    best_d = abs(best - m)
    for x in pool_sorted:
        d = abs(x - m)
        if d < best_d:
            best_d = d
            best = x
    return best

def _build_pool(root: str, scale: str, lo: int, hi: int) -> List[int]:
    pcs = set(_scale_pcs(root, scale))
    if not pcs:
        return []
    pool = [m for m in range(lo, hi + 1) if (m % 12) in pcs]
    if not pool:
        # fallback: snapped chromatic
        pool = [_snap_to_scale(m, pcs) for m in range(lo, hi + 1)]
        pool = list(sorted(set([m for m in pool if 0 <= m <= 127])))
    return list(sorted(pool))

def _choose_steps_modern(rng: random.Random, total_steps: int, grid: int, density: float, syncopation: float) -> List[int]:
    """
    Pick step onsets with a bias toward offbeats when syncopation is high.
    We treat "on-beat" as multiples of 4 (16th grid), "off-beat" otherwise.
    """
    grid = max(1, int(grid))
    density = max(0.0, min(1.0, float(density)))
    syncopation = max(0.0, min(1.0, float(syncopation)))

    steps = []
    for s in range(0, total_steps, grid):
        # base hit chance
        p = density
        is_on = (s % 4 == 0)
        # move probability mass from on-beat to off-beat
        if is_on:
            p *= (1.0 - 0.65 * syncopation)
        else:
            p *= (1.0 + 0.85 * syncopation)
        if rng.random() < p:
            steps.append(s)
    return steps


# -------------------------- Base MIDI blocks --------------------------

class MidiBlock:
    KIND = "fx"
    PARAMS: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def process_instrument(track_settings: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
        return track_settings

    @staticmethod
    def process_generate(notes: List[Dict[str, Any]], seq: Any, params: Dict[str, Any], track_settings: Dict[str, Any]) -> List[Dict[str, Any]]:
        # generator blocks override this
        return notes

    @staticmethod
    def process_notes(notes: List[Dict[str, Any]], seq: Any, params: Dict[str, Any], track_settings: Dict[str, Any]) -> List[Dict[str, Any]]:
        return notes

# -------------------------- Instrument/settings --------------------------

@BLOCKS.register("midi_program")
class MidiProgramBlock(MidiBlock):
    KIND = "instrument"
    PARAMS = {
        "channel": {"type": "int", "min": 0, "max": 15, "step": 1, "default": 0},
        "program": {"type": "int", "min": 0, "max": 127, "step": 1, "default": 0},
        "base_velocity": {"type": "int", "min": 1, "max": 127, "step": 1, "default": 90},
    }

    @staticmethod
    def process_instrument(track_settings: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
        s = dict(track_settings)
        s["channel"] = _clamp_int(params.get("channel", s.get("channel", 0)), 0, 15)
        s["program"] = _clamp_int(params.get("program", s.get("program", 0)), 0, 127)
        s["base_velocity"] = _clamp_int(params.get("base_velocity", s.get("base_velocity", 90)), 1, 127)
        return s

# -------------------------- Generators --------------------------

@BLOCKS.register("chord_progression")
class ChordProgressionGen(MidiBlock):
    KIND = "generator"
    PARAMS = {
        "root": {"type": "choice", "choices": NOTES, "default": "C"},
        "scale": {"type": "choice", "choices": sorted(SCALE_INTERVALS.keys()), "default": "major"},
        "progression": {"type": "str", "default": "1,5,6,4"},  # diatonic degrees
        "octave": {"type": "int", "min": 0, "max": 8, "step": 1, "default": 4},
        "chord": {"type": "choice", "choices": ["triad", "seventh"], "default": "triad"},
        "steps_per_chord": {"type": "int", "min": 1, "max": 64, "step": 1, "default": 16},
        "velocity": {"type": "int", "min": 1, "max": 127, "step": 1, "default": 90},
        "length_pct": {"type": "float", "min": 0.1, "max": 1.0, "step": 0.01, "default": 0.95, "decimals": 3},
    }

    @staticmethod
    def process_generate(notes, seq, params, track_settings):
        total = int(seq.total_steps())
        root = str(params.get("root", "C"))
        scale = str(params.get("scale", "major"))
        pcs = _scale_pcs(root, scale)  # list of pcs in scale (length 5-7)
        if not pcs:
            return notes

        prog_s = str(params.get("progression", "1,5,6,4"))
        degrees = []
        for part in prog_s.replace(" ", "").split(","):
            if not part:
                continue
            try:
                d = int(part)
                degrees.append(d)
            except Exception:
                pass
        if not degrees:
            degrees = [1, 5, 6, 4]

        octave = _clamp_int(params.get("octave", 4), 0, 8)
        chord_kind = str(params.get("chord", "triad"))
        spc = int(max(1, int(params.get("steps_per_chord", 16))))
        vel = _clamp_int(params.get("velocity", track_settings.get("base_velocity", 90)), 1, 127)
        length_pct = float(params.get("length_pct", 0.95))
        length_pct = max(0.1, min(1.0, length_pct))

        # build scale degrees into midi around octave
        base_c = (octave + 1) * 12  # C of that octave
        # root midi = nearest note in scale to base_c + root pc
        root_pc = _note_to_pc(root)
        # pick root midi as base_c + root_pc
        root_midi = _clamp_int(base_c + root_pc, 0, 127)

        out = []
        step = 0
        deg_i = 0
        while step < total:
            deg = degrees[deg_i % len(degrees)]
            deg_i += 1
            # diatonic degree index (1..7) -> scale index 0..len(pcs)-1 (wrap)
            si = (deg - 1) % len(pcs)
            # chord root pc
            chord_pc = pcs[si]
            chord_root = _snap_to_scale((root_midi - (root_midi % 12)) + chord_pc, set(pcs))

            # diatonic stack: 1-3-5-(7)
            chord_pcs = []
            chord_pcs.append(pcs[si])
            chord_pcs.append(pcs[(si + 2) % len(pcs)])
            chord_pcs.append(pcs[(si + 4) % len(pcs)])
            if chord_kind == "seventh" and len(pcs) >= 4:
                chord_pcs.append(pcs[(si + 6) % len(pcs)])

            # convert to midi notes, ascending, close to chord_root octave
            mids = []
            base_oct = (chord_root // 12) * 12
            last = -999
            for pc in chord_pcs:
                m = base_oct + pc
                while m <= last:
                    m += 12
                last = m
                mids.append(_clamp_int(m, 0, 127))

            chord_len = min(spc, total - step)
            note_len = max(1, int(round(chord_len * length_pct)))

            for m in mids:
                out.append({
                    "start_step": int(step),
                    "length_steps": int(note_len),
                    "midi": int(m),
                    "velocity": int(vel),
                })

            step += spc

        return notes + out


@BLOCKS.register("euclid_drums")
class EuclidDrumsGen(MidiBlock):
    KIND = "generator"
    PARAMS = {
        "midi_note": {"type": "int", "min": 0, "max": 127, "step": 1, "default": 36},   # kick=36, snare=38, hat=42
        "steps": {"type": "int", "min": 4, "max": 256, "step": 1, "default": 16},
        "pulses": {"type": "int", "min": 0, "max": 256, "step": 1, "default": 4},
        "rotation": {"type": "int", "min": 0, "max": 255, "step": 1, "default": 0},
        "velocity": {"type": "int", "min": 1, "max": 127, "step": 1, "default": 95},
        "length_steps": {"type": "int", "min": 1, "max": 16, "step": 1, "default": 1},
    }

    @staticmethod
    def process_generate(notes, seq, params, track_settings):
        total = int(seq.total_steps())
        midi_note = _clamp_int(params.get("midi_note", 36), 0, 127)
        steps = int(max(1, int(params.get("steps", 16))))
        pulses = int(max(0, int(params.get("pulses", 4))))
        pulses = min(steps, pulses)
        rot = int(max(0, int(params.get("rotation", 0)))) % steps
        vel = _clamp_int(params.get("velocity", track_settings.get("base_velocity", 95)), 1, 127)
        ln = int(max(1, int(params.get("length_steps", 1))))

        pat = _bjorklund(steps, pulses)
        if rot:
            pat = pat[-rot:] + pat[:-rot]

        out = []
        # tile across whole sequence
        for s in range(total):
            if pat[s % steps] == 1:
                out.append({
                    "start_step": int(s),
                    "length_steps": int(min(ln, total - s)),
                    "midi": int(midi_note),
                    "velocity": int(vel),
                })

        return notes + out


@BLOCKS.register("random_melody")
class RandomMelodyGen(MidiBlock):
    KIND = "generator"
    PARAMS = {
        "root": {"type": "choice", "choices": NOTES, "default": "C"},
        "scale": {"type": "choice", "choices": sorted(SCALE_INTERVALS.keys()), "default": "major"},
        "low_midi": {"type": "int", "min": 0, "max": 127, "step": 1, "default": 60},   # C4
        "high_midi": {"type": "int", "min": 0, "max": 127, "step": 1, "default": 84},  # C6
        "density": {"type": "float", "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.35, "decimals": 3},
        "min_len": {"type": "int", "min": 1, "max": 32, "step": 1, "default": 1},
        "max_len": {"type": "int", "min": 1, "max": 32, "step": 1, "default": 4},
        "seed": {"type": "int", "min": 0, "max": 999999, "step": 1, "default": 0},
        "velocity": {"type": "int", "min": 1, "max": 127, "step": 1, "default": 90},
    }

    @staticmethod
    def process_generate(notes, seq, params, track_settings):
        total = int(seq.total_steps())
        root = str(params.get("root", "C"))
        scale = str(params.get("scale", "major"))
        pcs_list = _scale_pcs(root, scale)
        pcs = set(pcs_list)
        if not pcs:
            return notes

        lo = _clamp_int(params.get("low_midi", 60), 0, 127)
        hi = _clamp_int(params.get("high_midi", 84), 0, 127)
        if hi < lo:
            lo, hi = hi, lo

        density = float(params.get("density", 0.35))
        density = max(0.0, min(1.0, density))

        min_len = int(max(1, int(params.get("min_len", 1))))
        max_len = int(max(min_len, int(params.get("max_len", 4))))

        seed = int(params.get("seed", 0))
        vel = _clamp_int(params.get("velocity", track_settings.get("base_velocity", 90)), 1, 127)

        rng = random.Random(seed)

        # precompute scale tones in range
        pool = [m for m in range(lo, hi + 1) if (m % 12) in pcs]
        if not pool:
            # fallback: snap chromatic
            pool = [ _snap_to_scale(m, pcs) for m in range(lo, hi + 1) ]
            pool = list(sorted(set([m for m in pool if 0 <= m <= 127])))

        out = []
        step = 0
        while step < total:
            if rng.random() < density:
                ln = rng.randint(min_len, max_len)
                ln = min(ln, total - step)
                m = int(rng.choice(pool))
                out.append({
                    "start_step": int(step),
                    "length_steps": int(max(1, ln)),
                    "midi": int(m),
                    "velocity": int(vel),
                })
                # hop by at least 1 step; small rests happen naturally if density < 1
            step += 1

        return notes + out

@BLOCKS.register("motif_variation_melody")
class MotifVariationMelodyGen(MidiBlock):
    """
    Short motif repeated across the song with controlled variation:
      - good for hooky top-lines
      - uses scale tones, small steps + occasional leaps
      - optional stutters and octave pops
    """
    KIND = "generator"
    PARAMS = {
        "root": {"type": "choice", "choices": NOTES, "default": "C"},
        "scale": {"type": "choice", "choices": sorted(SCALE_INTERVALS.keys()), "default": "natural_minor"},
        "low_midi": {"type": "int", "min": 0, "max": 127, "step": 1, "default": 60},   # C4
        "high_midi": {"type": "int", "min": 0, "max": 127, "step": 1, "default": 84},  # C6
        "motif_steps": {"type": "int", "min": 4, "max": 64, "step": 1, "default": 8},
        "grid_steps": {"type": "int", "min": 1, "max": 8, "step": 1, "default": 1},     # 1=16ths, 2=8ths, 4=quarters
        "density": {"type": "float", "min": 0.05, "max": 1.0, "step": 0.01, "default": 0.55, "decimals": 3},
        "syncopation": {"type": "float", "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.55, "decimals": 3},
        "min_len": {"type": "int", "min": 1, "max": 16, "step": 1, "default": 1},
        "max_len": {"type": "int", "min": 1, "max": 32, "step": 1, "default": 3},
        "variation_prob": {"type": "float", "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.35, "decimals": 3},
        "octave_pop_prob": {"type": "float", "min": 0.0, "max": 0.6, "step": 0.01, "default": 0.12, "decimals": 3},
        "stutter_prob": {"type": "float", "min": 0.0, "max": 0.8, "step": 0.01, "default": 0.18, "decimals": 3},
        "seed": {"type": "int", "min": 0, "max": 999999, "step": 1, "default": 0},
        "velocity": {"type": "int", "min": 1, "max": 127, "step": 1, "default": 92},
    }

    @staticmethod
    def process_generate(notes, seq, params, track_settings):
        total = int(seq.total_steps())

        root = str(params.get("root", "C"))
        scale = str(params.get("scale", "natural_minor"))

        lo = _clamp_int(params.get("low_midi", 60), 0, 127)
        hi = _clamp_int(params.get("high_midi", 84), 0, 127)
        if hi < lo:
            lo, hi = hi, lo

        motif_steps = int(max(4, int(params.get("motif_steps", 8))))
        grid = int(max(1, int(params.get("grid_steps", 1))))

        density = float(params.get("density", 0.55))
        sync = float(params.get("syncopation", 0.55))
        min_len = int(max(1, int(params.get("min_len", 1))))
        max_len = int(max(min_len, int(params.get("max_len", 3))))

        var_p = float(params.get("variation_prob", 0.35))
        oct_p = float(params.get("octave_pop_prob", 0.12))
        stut_p = float(params.get("stutter_prob", 0.18))

        seed = int(params.get("seed", 0))
        vel = _clamp_int(params.get("velocity", track_settings.get("base_velocity", 92)), 1, 127)

        rng = random.Random(seed)
        pool = _build_pool(root, scale, lo, hi)
        if not pool:
            return notes

        # choose motif onsets within motif window
        motif_onsets = _choose_steps_modern(rng, motif_steps, grid, density, sync)
        if not motif_onsets:
            motif_onsets = list(range(0, motif_steps, max(1, grid)))

        # generate motif pitches using weighted interval steps
        # small moves favored, occasional leaps
        interval_choices = [(-2, 1.2), (-1, 2.0), (0, 1.0), (1, 2.2), (2, 1.2), (3, 0.5), (-3, 0.4), (5, 0.25), (-5, 0.2)]
        cur = int(rng.choice(pool))
        motif = []
        for s in motif_onsets:
            # sometimes hold the same pitch (sticky)
            if rng.random() < 0.25:
                nxt = cur
            else:
                step_int = int(_weighted_choice(rng, interval_choices))
                nxt = _nearest_in_pool(cur + step_int, pool)
            cur = nxt
            ln = rng.randint(min_len, max_len)
            ln = min(ln, motif_steps - s) if (s + ln) <= motif_steps else max(1, motif_steps - s)
            motif.append((int(s), int(ln), int(cur)))

        out = []
        for base in range(0, total, motif_steps):
            # apply variation per repetition
            rep_var = (rng.random() < var_p)

            # optional transpose by octave (pop)
            trans = 0
            if rep_var and (rng.random() < oct_p):
                trans = 12 if (rng.random() < 0.5) else -12

            # optional stutter: pick a motif note and duplicate very short repeats
            stutter_idx = -1
            if rep_var and motif and (rng.random() < stut_p):
                stutter_idx = rng.randrange(0, len(motif))

            for i, (ms, ml, mm) in enumerate(motif):
                start = base + ms
                if start >= total:
                    continue

                midi = _clamp_int(mm + trans, 0, 127)

                # small random pitch swap on variation
                if rep_var and rng.random() < 0.18:
                    midi = int(rng.choice(pool))

                length = int(min(ml, total - start))
                length = max(1, length)

                out.append({
                    "start_step": int(start),
                    "length_steps": int(length),
                    "midi": int(midi),
                    "velocity": int(vel),
                })

                # stutter: add 2-4 rapid repeats right after (like a quick roll)
                if i == stutter_idx:
                    rep_n = rng.randint(2, 4)
                    rep_len = 1
                    for k in range(rep_n):
                        ss = start + k
                        if ss >= total:
                            break
                        out.append({
                            "start_step": int(ss),
                            "length_steps": int(rep_len),
                            "midi": int(midi),
                            "velocity": _clamp_int(int(vel) - 8, 1, 127),
                        })

        out.sort(key=lambda z: (int(z["start_step"]), int(z["midi"])))
        return notes + out


@BLOCKS.register("call_response_melody")
class CallResponseMelodyGen(MidiBlock):
    """
    Phrase-based generator:
      - "call" phrase near the start of each bar/half-bar
      - "response" phrase later, leaving space between
    """
    KIND = "generator"
    PARAMS = {
        "root": {"type": "choice", "choices": NOTES, "default": "C"},
        "scale": {"type": "choice", "choices": sorted(SCALE_INTERVALS.keys()), "default": "natural_minor"},
        "low_midi": {"type": "int", "min": 0, "max": 127, "step": 1, "default": 62},
        "high_midi": {"type": "int", "min": 0, "max": 127, "step": 1, "default": 86},
        "call_len_steps": {"type": "int", "min": 2, "max": 32, "step": 1, "default": 6},
        "gap_steps": {"type": "int", "min": 0, "max": 32, "step": 1, "default": 4},
        "resp_len_steps": {"type": "int", "min": 2, "max": 32, "step": 1, "default": 6},
        "grid_steps": {"type": "int", "min": 1, "max": 8, "step": 1, "default": 1},
        "density": {"type": "float", "min": 0.05, "max": 1.0, "step": 0.01, "default": 0.55, "decimals": 3},
        "syncopation": {"type": "float", "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.6, "decimals": 3},
        "seed": {"type": "int", "min": 0, "max": 999999, "step": 1, "default": 0},
        "velocity": {"type": "int", "min": 1, "max": 127, "step": 1, "default": 95},
    }

    @staticmethod
    def process_generate(notes, seq, params, track_settings):
        total = int(seq.total_steps())
        spb = int(getattr(seq, "steps_per_bar", 16) or 16)

        root = str(params.get("root", "C"))
        scale = str(params.get("scale", "natural_minor"))

        lo = _clamp_int(params.get("low_midi", 62), 0, 127)
        hi = _clamp_int(params.get("high_midi", 86), 0, 127)
        if hi < lo:
            lo, hi = hi, lo

        call_len = int(max(2, int(params.get("call_len_steps", 6))))
        gap = int(max(0, int(params.get("gap_steps", 4))))
        resp_len = int(max(2, int(params.get("resp_len_steps", 6))))
        grid = int(max(1, int(params.get("grid_steps", 1))))
        density = float(params.get("density", 0.55))
        sync = float(params.get("syncopation", 0.6))

        seed = int(params.get("seed", 0))
        vel = _clamp_int(params.get("velocity", track_settings.get("base_velocity", 95)), 1, 127)

        rng = random.Random(seed)
        pool = _build_pool(root, scale, lo, hi)
        if not pool:
            return notes

        # helper: generate a phrase in [start, start+length)
        def gen_phrase(start_step: int, length_steps: int, anchor: int) -> List[Dict[str, Any]]:
            length_steps = max(1, int(length_steps))
            onsets = _choose_steps_modern(rng, length_steps, grid, density, sync)
            if not onsets:
                onsets = [0]
            cur = anchor
            outp = []
            for rel in onsets:
                s = start_step + rel
                if s >= total:
                    continue
                # melody motion around anchor with small steps
                move = int(_weighted_choice(rng, [(-2, 1.2), (-1, 2.2), (0, 1.0), (1, 2.2), (2, 1.2), (4, 0.35), (-4, 0.3)]))
                cur = _nearest_in_pool(cur + move, pool)
                # lengths: short, rhythmic
                ln = int(_weighted_choice(rng, [(1, 2.2), (2, 1.6), (3, 0.7), (4, 0.35)]))
                ln = min(ln, (start_step + length_steps) - s, total - s)
                ln = max(1, int(ln))
                outp.append({
                    "start_step": int(s),
                    "length_steps": int(ln),
                    "midi": int(cur),
                    "velocity": int(vel),
                })
            return outp

        out = []
        for bar_start in range(0, total, spb):
            # pick bar anchor
            anchor = int(rng.choice(pool))

            # call at bar_start
            call = gen_phrase(bar_start, min(call_len, total - bar_start), anchor)

            # response later with gap
            resp_start = bar_start + call_len + gap
            if resp_start < bar_start + spb and resp_start < total:
                # response anchor shifts a bit
                resp_anchor = _nearest_in_pool(anchor + int(_weighted_choice(rng, [(-5, 0.4), (-3, 0.9), (-2, 1.3), (2, 1.3), (3, 0.9), (5, 0.4)])), pool)
                resp = gen_phrase(resp_start, min(resp_len, total - resp_start), resp_anchor)
            else:
                resp = []

            out.extend(call)
            out.extend(resp)

        out.sort(key=lambda z: (int(z["start_step"]), int(z["midi"])))
        return notes + out


@BLOCKS.register("glide_grace_melody")
class GlideGraceMelodyGen(MidiBlock):
    """
    "Glide" feel WITHOUT pitch-bend:
      - emits a tiny grace note (often 1 step) then the target note
      - works great with plucks/leads and short gates
    """
    KIND = "generator"
    PARAMS = {
        "root": {"type": "choice", "choices": NOTES, "default": "C"},
        "scale": {"type": "choice", "choices": sorted(SCALE_INTERVALS.keys()), "default": "natural_minor"},
        "low_midi": {"type": "int", "min": 0, "max": 127, "step": 1, "default": 62},
        "high_midi": {"type": "int", "min": 0, "max": 127, "step": 1, "default": 86},
        "density": {"type": "float", "min": 0.05, "max": 1.0, "step": 0.01, "default": 0.45, "decimals": 3},
        "grid_steps": {"type": "int", "min": 1, "max": 8, "step": 1, "default": 1},
        "grace_len": {"type": "int", "min": 1, "max": 4, "step": 1, "default": 1},
        "target_len": {"type": "int", "min": 1, "max": 16, "step": 1, "default": 2},
        "grace_range_semitones": {"type": "int", "min": 1, "max": 7, "step": 1, "default": 2},
        "syncopation": {"type": "float", "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.65, "decimals": 3},
        "seed": {"type": "int", "min": 0, "max": 999999, "step": 1, "default": 0},
        "velocity": {"type": "int", "min": 1, "max": 127, "step": 1, "default": 92},
    }

    @staticmethod
    def process_generate(notes, seq, params, track_settings):
        total = int(seq.total_steps())
        root = str(params.get("root", "C"))
        scale = str(params.get("scale", "natural_minor"))

        lo = _clamp_int(params.get("low_midi", 62), 0, 127)
        hi = _clamp_int(params.get("high_midi", 86), 0, 127)
        if hi < lo:
            lo, hi = hi, lo

        density = float(params.get("density", 0.45))
        grid = int(max(1, int(params.get("grid_steps", 1))))
        grace_len = int(max(1, int(params.get("grace_len", 1))))
        target_len = int(max(1, int(params.get("target_len", 2))))
        grace_rng = int(max(1, int(params.get("grace_range_semitones", 2))))
        sync = float(params.get("syncopation", 0.65))
        seed = int(params.get("seed", 0))
        vel = _clamp_int(params.get("velocity", track_settings.get("base_velocity", 92)), 1, 127)

        rng = random.Random(seed)
        pool = _build_pool(root, scale, lo, hi)
        if not pool:
            return notes

        onsets = _choose_steps_modern(rng, total, grid, density, sync)
        if not onsets:
            return notes

        out = []
        for s in onsets:
            if s >= total:
                continue
            target = int(rng.choice(pool))

            # grace note is usually below or above within a small semitone range,
            # then snapped back to pool for musicality
            sign = -1 if (rng.random() < 0.6) else 1
            grace = _nearest_in_pool(target + sign * rng.randint(1, grace_rng), pool)

            # place grace slightly before target if possible
            gs = max(0, s - grace_len)
            gl = min(grace_len, total - gs)
            tl = min(target_len, total - s)

            if gl > 0 and gs < s:
                out.append({
                    "start_step": int(gs),
                    "length_steps": int(gl),
                    "midi": int(grace),
                    "velocity": _clamp_int(int(vel) - 10, 1, 127),
                })
            out.append({
                "start_step": int(s),
                "length_steps": int(max(1, tl)),
                "midi": int(target),
                "velocity": int(vel),
            })

        out.sort(key=lambda z: (int(z["start_step"]), int(z["midi"])))
        return notes + out


@BLOCKS.register("pentatonic_hook")
class PentatonicHookGen(MidiBlock):
    """
    Catchy hook generator biased toward pentatonic tones and repetition.
    Great for simple, sticky leads.
    """
    KIND = "generator"
    PARAMS = {
        "root": {"type": "choice", "choices": NOTES, "default": "C"},
        "mode": {"type": "choice", "choices": ["major", "minor"], "default": "minor"},
        "low_midi": {"type": "int", "min": 0, "max": 127, "step": 1, "default": 60},
        "high_midi": {"type": "int", "min": 0, "max": 127, "step": 1, "default": 84},
        "grid_steps": {"type": "int", "min": 1, "max": 8, "step": 1, "default": 1},
        "density": {"type": "float", "min": 0.05, "max": 1.0, "step": 0.01, "default": 0.5, "decimals": 3},
        "repeat_prob": {"type": "float", "min": 0.0, "max": 0.9, "step": 0.01, "default": 0.45, "decimals": 3},
        "seed": {"type": "int", "min": 0, "max": 999999, "step": 1, "default": 0},
        "velocity": {"type": "int", "min": 1, "max": 127, "step": 1, "default": 94},
    }

    @staticmethod
    def process_generate(notes, seq, params, track_settings):
        total = int(seq.total_steps())
        root = str(params.get("root", "C"))
        mode = str(params.get("mode", "minor"))

        scale = "pentatonic_minor" if mode == "minor" else "pentatonic_major"

        lo = _clamp_int(params.get("low_midi", 60), 0, 127)
        hi = _clamp_int(params.get("high_midi", 84), 0, 127)
        if hi < lo:
            lo, hi = hi, lo

        grid = int(max(1, int(params.get("grid_steps", 1))))
        density = float(params.get("density", 0.5))
        rep_p = float(params.get("repeat_prob", 0.45))
        seed = int(params.get("seed", 0))
        vel = _clamp_int(params.get("velocity", track_settings.get("base_velocity", 94)), 1, 127)

        rng = random.Random(seed)
        pool = _build_pool(root, scale, lo, hi)
        if not pool:
            return notes

        onsets = _choose_steps_modern(rng, total, grid, density, syncopation=0.55)
        if not onsets:
            return notes

        out = []
        last = int(rng.choice(pool))
        for s in onsets:
            if rng.random() < rep_p:
                midi = last
            else:
                # small moves are favored
                midi = _nearest_in_pool(last + int(_weighted_choice(rng, [(-2, 1.4), (-1, 2.1), (1, 2.1), (2, 1.4), (5, 0.3), (-5, 0.25)])), pool)
            last = midi

            ln = int(_weighted_choice(rng, [(1, 2.4), (2, 1.6), (3, 0.6), (4, 0.3)]))
            ln = min(ln, total - s)
            ln = max(1, ln)

            out.append({
                "start_step": int(s),
                "length_steps": int(ln),
                "midi": int(midi),
                "velocity": int(vel),
            })

        out.sort(key=lambda z: (int(z["start_step"]), int(z["midi"])))
        return notes + out


# -------------------------- FX (transform notes) --------------------------

@BLOCKS.register("transpose")
class TransposeFx(MidiBlock):
    KIND = "fx"
    PARAMS = {"semitones": {"type": "int", "min": -48, "max": 48, "step": 1, "default": 0}}

    @staticmethod
    def process_notes(notes, seq, params, track_settings):
        semi = int(params.get("semitones", 0))
        if semi == 0:
            return notes
        out = []
        for n in notes:
            nn = dict(n)
            nn["midi"] = _clamp_int(int(nn["midi"]) + semi, 0, 127)
            out.append(nn)
        return out


@BLOCKS.register("quantize")
class QuantizeFx(MidiBlock):
    KIND = "fx"
    PARAMS = {
        "grid_steps": {"type": "int", "min": 1, "max": 64, "step": 1, "default": 1},
        "mode": {"type": "choice", "choices": ["nearest", "down", "up"], "default": "nearest"},
        "quantize_length": {"type": "bool", "default": False},
    }

    @staticmethod
    def process_notes(notes, seq, params, track_settings):
        g = max(1, int(params.get("grid_steps", 1)))
        mode = str(params.get("mode", "nearest"))
        qlen = bool(params.get("quantize_length", False))
        total = int(seq.total_steps())

        def q_step(x: int) -> int:
            if g <= 1:
                return x
            if mode == "down":
                return (x // g) * g
            if mode == "up":
                return ((x + g - 1) // g) * g
            lo = (x // g) * g
            hi = ((x + g - 1) // g) * g
            return lo if (x - lo) <= (hi - x) else hi

        out = []
        for n in notes:
            nn = dict(n)
            s = q_step(int(nn["start_step"]))
            s = max(0, min(total - 1, s))
            nn["start_step"] = s
            if qlen:
                nn["length_steps"] = max(1, q_step(int(nn["length_steps"])))
            nn["length_steps"] = max(1, min(int(nn["length_steps"]), total - int(nn["start_step"])))
            out.append(nn)
        out.sort(key=lambda z: (int(z["start_step"]), int(z["midi"])))
        return out


@BLOCKS.register("humanize")
class HumanizeFx(MidiBlock):
    KIND = "fx"
    PARAMS = {
        "timing_jitter_steps": {"type": "float", "min": 0.0, "max": 2.0, "step": 0.05, "default": 0.0, "decimals": 3},
        "velocity_jitter": {"type": "int", "min": 0, "max": 50, "step": 1, "default": 0},
        "seed": {"type": "int", "min": 0, "max": 999999, "step": 1, "default": 0},
    }

    @staticmethod
    def process_notes(notes, seq, params, track_settings):
        tj = float(params.get("timing_jitter_steps", 0.0))
        vj = int(params.get("velocity_jitter", 0))
        seed = int(params.get("seed", 0))
        if tj <= 0 and vj <= 0:
            return notes
        total = int(seq.total_steps())
        rng = random.Random(seed)

        out = []
        for n in notes:
            nn = dict(n)
            if tj > 0:
                delta = rng.uniform(-tj, tj)
                s = int(round(float(nn["start_step"]) + delta))
                s = max(0, min(total - 1, s))
                nn["start_step"] = s
                nn["length_steps"] = max(1, min(int(nn["length_steps"]), total - int(nn["start_step"])))
            if vj > 0:
                dv = rng.randint(-vj, vj)
                nn["velocity"] = _clamp_int(int(nn.get("velocity", 90)) + dv, 1, 127)
            out.append(nn)

        out.sort(key=lambda z: (int(z["start_step"]), int(z["midi"])))
        return out


@BLOCKS.register("velocity")
class VelocityFx(MidiBlock):
    KIND = "fx"
    PARAMS = {
        "add": {"type": "int", "min": -64, "max": 64, "step": 1, "default": 0},
        "mul": {"type": "float", "min": 0.1, "max": 2.0, "step": 0.01, "default": 1.0, "decimals": 3},
    }

    @staticmethod
    def process_notes(notes, seq, params, track_settings):
        add = int(params.get("add", 0))
        mul = float(params.get("mul", 1.0))
        if add == 0 and abs(mul - 1.0) < 1e-9:
            return notes
        out = []
        for n in notes:
            nn = dict(n)
            v = float(nn.get("velocity", track_settings.get("base_velocity", 90))) * mul + float(add)
            nn["velocity"] = _clamp_int(int(round(v)), 1, 127)
            out.append(nn)
        return out


@BLOCKS.register("scale_lock")
class ScaleLockFx(MidiBlock):
    KIND = "fx"
    PARAMS = {
        "root": {"type": "choice", "choices": NOTES, "default": "C"},
        "scale": {"type": "choice", "choices": sorted(SCALE_INTERVALS.keys()), "default": "major"},
        "mode": {"type": "choice", "choices": ["snap", "remove"], "default": "snap"},
    }

    @staticmethod
    def process_notes(notes, seq, params, track_settings):
        root = str(params.get("root", "C"))
        scale = str(params.get("scale", "major"))
        mode = str(params.get("mode", "snap"))
        pcs = set(_scale_pcs(root, scale))
        if not pcs:
            return notes

        out = []
        for n in notes:
            midi = int(n["midi"])
            if (midi % 12) in pcs:
                out.append(n)
            else:
                if mode == "remove":
                    continue
                nn = dict(n)
                nn["midi"] = _snap_to_scale(midi, pcs)
                out.append(nn)

        out.sort(key=lambda z: (int(z["start_step"]), int(z["midi"])))
        return out


@BLOCKS.register("arpeggiate")
class ArpeggiateFx(MidiBlock):
    KIND = "fx"
    PARAMS = {
        "subdiv_steps": {"type": "int", "min": 1, "max": 16, "step": 1, "default": 2},  # 2 = 8th notes if step=16th
        "pattern": {"type": "choice", "choices": ["up", "down", "updown", "random"], "default": "up"},
        "gate": {"type": "float", "min": 0.1, "max": 1.0, "step": 0.01, "default": 0.9, "decimals": 3},
        "seed": {"type": "int", "min": 0, "max": 999999, "step": 1, "default": 0},
        "min_chord_notes": {"type": "int", "min": 2, "max": 8, "step": 1, "default": 3},
    }

    @staticmethod
    def process_notes(notes, seq, params, track_settings):
        subdiv = int(max(1, int(params.get("subdiv_steps", 2))))
        pattern = str(params.get("pattern", "up"))
        gate = float(params.get("gate", 0.9))
        gate = max(0.1, min(1.0, gate))
        seed = int(params.get("seed", 0))
        min_ch = int(max(2, int(params.get("min_chord_notes", 3))))
        rng = random.Random(seed)

        # group by start_step
        by_step: Dict[int, List[Dict[str, Any]]] = {}
        for n in notes:
            by_step.setdefault(int(n["start_step"]), []).append(n)

        out: List[Dict[str, Any]] = []
        for s, group in by_step.items():
            # only arp if it's chord-ish
            if len(group) < min_ch:
                out.extend(group)
                continue

            group_sorted = sorted(group, key=lambda z: int(z["midi"]))
            chord_len = max(1, max(int(z["length_steps"]) for z in group_sorted))
            steps = list(range(int(s), int(s) + chord_len, subdiv))
            if not steps:
                out.extend(group_sorted)
                continue

            mids = [int(z["midi"]) for z in group_sorted]
            if pattern == "down":
                order = list(reversed(mids))
            elif pattern == "updown":
                order = mids + list(reversed(mids[1:-1] if len(mids) > 2 else mids))
            elif pattern == "random":
                order = mids[:]
                rng.shuffle(order)
            else:
                order = mids

            vel = int(group_sorted[0].get("velocity", track_settings.get("base_velocity", 90)))
            ch = int(group_sorted[0].get("channel", track_settings.get("channel", 0)))

            for i, ss in enumerate(steps):
                m = int(order[i % len(order)])
                ln = max(1, int(round(subdiv * gate)))
                out.append({
                    "start_step": int(ss),
                    "length_steps": int(ln),
                    "midi": int(m),
                    "velocity": _clamp_int(vel, 1, 127),
                    "channel": _clamp_int(ch, 0, 15),
                })

        out.sort(key=lambda z: (int(z["start_step"]), int(z["midi"])))
        return out

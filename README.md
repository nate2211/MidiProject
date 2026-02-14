Dynamic MIDI Generation Pipeline for FL Studio & Beyond

midicreator is a modular, block-based MIDI generation tool designed for rapid electronic music composition. Built with Python 3 and PyQt6, it allows producers to build complex musical sequences by stacking "Generators" (to create notes) and "FX" (to transform them) in a non-destructive pipeline.

While designed with the FL Studio workflow in mind, the exported MIDI files are standard and compatible with any modern DAW (Ableton Live, Logic Pro, Bitwig, etc.).
🚀 Key Features

    Modular Block Registry: Easily swap between different generation algorithms and MIDI processors.

    Algorithmic Generators: * Euclidean Rhythm: Generate complex polyrhythms using Bjorklund’s algorithm.

        Motif Variation: Create hooky, repetitive leads with controlled randomness and "stutters."

        Chord Progressions: Procedural diatonic chord generation with customizable voicings.

        Call & Response: Phrase-based generation that mimics musical dialogue.

    Real-time FX Stack: Apply MIDI transforms like Scale Locking, Quantization, Arpeggiation, and Humanization (timing/velocity jitter) on the fly.

    Piano Roll Preview: High-performance PyQt6 graphics view to visualize generated notes before exporting.

    Multi-Track Export: Build full arrangements and export individual tracks or the entire sequence as a MIDI file.

    Auto-Regen: See and hear changes instantly with the automatic generation engine.

🛠️ Installation & Usage

    Clone the repository:
    Bash

    git clone https://github.com/yourusername/midicreator.git
    cd midicreator

    Install dependencies:
    Bash

    pip install numpy PyQt6

    Run the application:
    Bash

    python gui.py

🎹 Workflow

    Add a Track: Each track has its own stack of blocks.

    Pick a Generator: Choose how notes are born (e.g., random_melody or euclid_drums).

    Add FX: Transform the output (e.g., use scale_lock to keep things in key or arpeggiate to add movement).

    Export: Save as .mid and drag it directly into your DAW.

🏗️ Extending the Project

Adding a new musical algorithm is as simple as creating a new class in blocks.py and decorating it with @BLOCKS.register("your_block_name"). The UI automatically generates sliders and toggles based on your defined PARAMS schema.

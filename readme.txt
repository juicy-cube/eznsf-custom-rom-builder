This document describes only the changes made relative to "Wonder Box"
(the one that added multi-NSF albums, bank-pinning, the redesigned menu, etc.).
If you are interested in the changes that fork made compared to the original
EZNSF, please read its readme:

  https://github.com/juicy-cube/wonder-box


CHANGES SINCE THE PREVIOUS FORK
=================================

1. Scrollable track list (eznsf.s)
------------------------------------

The menu used to draw every entry in the album in one pass, at a fixed
row height, with no limit. Once an album had enough tracks to run past
the bottom of the screen, the draw code kept writing anyway and spilled
into the attribute table (and beyond), which showed up as corrupted
colors/graphics on the tracks screen.

The list now only draws a window of eNSF::LISTROWS rows (20 by default)
and scrolls that window to follow the cursor as it moves past either
edge, redrawing just the visible rows each time the window shifts. Each
redrawn row is blanked before the new string is written, so a shorter
title can't leave stale tiles behind from whatever used to occupy that
row. This removes the practical limit on album/track count.

Window size is configurable per album via:

    LISTROWS 20

New BSS variables: list_scroll, list_rows_left, list_row_idx. New
procedures: list_clamp_scroll, list_draw_body, list_draw_row,
ppu_clear_row. mode_list and list_sprite were rewritten around these;
everything else on that screen (title/copyright header, PLAY ALL / INFO
entries, button handling) is unchanged.


2. NSF Builder GUI (builder_gui.py) -- new tool
---------------------------------------------------

A Tkinter application for assembling an album.txt without hand-editing
it. Drag-and-drop (or manually add) NSF files onto a tracklist; each
song's subsongs are read from the NSF header and added as rows you can
reorder, edit in place (double-click a cell), or delete. ROM-wide
settings (title, copyright, PLAY ALL / INFO labels, loop time, the INFO
screen text) are editable fields at the top. "Load Album" reopens an
existing album.txt back into the tracklist for further editing; "Build
ROM" writes album.txt and runs eznsf.py.

Notes specific to this tool:

  - NSF files can be added from anywhere on disk, not just the project
    folder -- the tracklist only displays each file's name, but tracks
    its real path separately, and resolves relative paths found when
    loading an existing album.txt against that album.txt's own folder
    if they don't resolve against the current directory.
  - A line whose first non-blank character is '#' is a comment; '#'
    anywhere else (e.g. inside a path like "Album #1") is treated as
    literal text, both here and in eznsf.py's own album.txt parser.
  - Building runs eznsf.py directly as a background process and waits
    for it to actually finish before reporting anything.
    Success or failure is decided by eznsf.py's real exit code,
    and the dialog shown either lists each NSF's outcome (see AUTOFIX below)
    or, on failure, the tail of eznsf.py's own error output.
  - Two checkboxes, described in their own sections below: AUTOFIX and
    AUTONUMERATE.


3. NSF memory-conflict patcher (nsf_patch.py) -- new tool
---------------------------------------------------------------

Some NSFs simply can't share a ROM with this (or any) standalone NSF
driver: their own compiled sound engine keeps working variables in the
same zero page ($00FA-$00FF) or RAM ($0400-$07FF) this driver reserves
for itself, and the two silently stomp on each other -- symptoms range
from a track never starting to corrupted audio or graphics part-way
through.

nsf_patch.py finds and fixes that automatically where it safely can, by
actually running the tune:

  - It loads the NSF into a real 6502 emulator (the `py65` package),
    calls INIT and then several thousand simulated PLAY frames (across
    every subsong, capped at 8), using the same bank-window model this
    driver uses ($5FF8-$5FFF switching 4K pages into $8000-$FFFF).
  - Every instruction that touches the reserved ranges through direct
    addressing (LDA/STA/... $addr, $addr,X, $addr,Y) is recorded. These
    get relocated: all addresses in one contiguous touched region move
    by the same fixed offset, into whatever's actually free ($0200-
    $03FF for RAM; unused zero-page bytes for zero page), so indexed
    and table-driven access keeps working exactly as before, just at a
    different address.
  - Indirect addressing ((zp),Y / (zp,X)) is reported but never
    auto-patched -- the target address there is data, not an
    instruction operand, so guessing at it risks silently producing a
    ROM that's wrong in some subtler way than the original bug. This
    covers a meaningful share of the failures we saw (hand-optimized
    game-rip sound drivers lean on it more than FamiTracker exports
    do); those still need a person to look at them.
  - Every patch is self-checked before it's accepted: the patched ROM
    is re-run through the same simulation and must (a) touch zero
    reserved addresses and (b) execute the exact same sequence of
    instructions as the original, in the same order. If that doesn't
    hold -- e.g. because a reserved address turned out to be read
    through an indirect pointer somewhere the direct-only patch didn't
    catch -- the file is left alone rather than shipped half-fixed.

Usable two ways:

  - Standalone: `python nsf_patch.py song.nsf [more.nsf ...]` prints a
    per-file report and, for anything it could fix, writes a patched
    copy.
  - From an album build, via a new directive:

        AUTOFIX 1

    which builder_gui.py's "AUTOFIX NSFs" checkbox writes for you. When
    on, eznsf.py runs the same check on every NSF in the album before
    assembling anything. Anything it can fix gets a copy written to a
    _patched_nsfs/ folder next to album.txt, named
    <original name>.patched.nsf, and that copy is what actually gets
    built -- the original file on disk is never modified. Anything it
    can't safely fix is dropped from the build entirely (that NSF and
    its tracks are removed, the remaining albums/tracks are
    renumbered), rather than either aborting the whole build or quietly
    shipping a broken track. If every NSF ends up dropped, the build
    stops with an error instead of producing an empty ROM.

  Requires the `py65` package (`pip install py65`) wherever eznsf.py
  actually runs; AUTOFIX is skipped with a clear error if it's missing.

  eznsf.py also prints a small machine-readable block at the end of its
  output (between "==NSF_STATUS==" and "==END_NSF_STATUS==". one line
  per NSF: name|patched/skipped/unchanged|detail) that builder_gui.py
  reads to show a plain per-file summary instead of the full build log.


4. AUTONUMERATE
-------------------

    AUTONUMERATE 1

prefixes each track's position ("01. ", "02. ", ...) on the tracklist
menu only. The now-playing screen still shows the plain title -- the
two screens were changed to read from separate generated strings
(list_track_NN for the menu, track_NN for now-playing) specifically so
numbering one couldn't leak into the other. builder_gui.py's
"AUTONUMERATE" checkbox writes the directive; the numbering itself
happens in eznsf.py at build time, not by editing any title text in the
GUI or in album.txt.


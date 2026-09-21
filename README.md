# EZNSF Custom ROM Builder
A fork of "Wonder Box" (https://github.com/juicy-cube/wonder-box)
letting you build your own NES music albums

New Tool: NSF Builder GUI (builder_gui.py)
-
A Tkinter application for assembling an album.txt and build complete ROM.
Drag-and-drop (or manually add) NSF files onto a tracklist, organize them
and modify various album settings

New Tool: NSF memory-conflict patcher (nsf_patch.py)
-
Some NSFs simply can't share a ROM with this (or any) standalone NSF
driver. This tool finds and fixes that automatically where it safely can.
Tested on ~80 files, sometimes it works, sometimes it doesn't, sometimes
it causes errors and glitches. Needs further development.

Also, it attempts to enforce the region (replay speed) regardless of
the console the ROM is played on, for example, if you want to listen
to a track written at NTSC speed on a PAL console.
This doesn't always work 100% of the time, and sometimes there can be
audible differences in pitch (especially when NSF uses ADPCM).
It all depends on the internal player used. I encourage you to test it.

— AceMan

CHANGELOG
-
2026.09.21: Experimental feature - forcing speed NTSC/PAL

2026.09.13: v1.0 Initial release

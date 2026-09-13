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

— AceMan

CHANGELOG
-
2026.09: v1.0 Initial release

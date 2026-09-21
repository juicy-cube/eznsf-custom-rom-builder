#!/usr/bin/env python3
import sys

if sys.version_info[0] < 3:
    print("Python 3 required.")
    sys.exit(1)

import os
import datetime
import shlex
import subprocess

album = "album.txt"
outdir = "temp"
ca65 = "tools/ca65.exe"
ld65 = "tools/ld65.exe"
output_nsfe = True

def strip_comment(line):
    # Comments have to occupy the whole line. A trailing "value # comment"
    # style can't be told apart from a value that legitimately contains a
    # '#' (e.g. a path like "C:\Music\Album #1\song.nsf") without a quoting
    # syntax this format doesn't have, so only a line whose first
    # non-blank character is '#' is treated as a comment.
    if line.lstrip().startswith("#"):
        return ""
    return line


def errmsg(msg):
    print("Error: " + msg)
    sys.exit(1)

def safe_tokens(line):
    # Whitespace-only split, no quote handling: apostrophes/quotes in titles,
    # artist names, etc. must not be treated as shlex quote characters.
    lex = shlex.shlex(line, posix=True)
    lex.whitespace_split = True
    lex.quotes = ""
    lex.escape = ""
    lex.commenters = ""
    return list(lex)

if len(sys.argv) > 1:
    album = sys.argv[1]
if len(sys.argv) > 2:
    outdir = sys.argv[2]
if len(sys.argv) > 3:
    errmsg("Error: Too many arguments on command line.\n" + \
        "Usage: eznsf.py [album] [directory]")

now_string = datetime.datetime.now().strftime("%a %b %d %H:%M:%S %Y")

nsf_albums = []
nsf_nrom = 0
nsf_title = ""
nsf_artist = ""
nsf_copyright = ""
nsf_info_text = "INFO"
nsf_playall_text = "PLAY ALL"
nsf_looptime_mins = 0
nsf_looptime_secs = 0
nsf_listrows = 20
nsf_autofix = False
nsf_autonumerate = False
nsf_tracks = []
current_tracks_temp = {}
nsf_screens = []
nsf_info = []
nsf_coord = []
nsf_const = []

try:
    os.makedirs(outdir)
except OSError:
    if not os.path.isdir(outdir):
        raise

for file in os.listdir(outdir):
    if \
          file.endswith(".bin") \
       or file.endswith(".sh") \
       or file.endswith(".o") \
       or file.endswith(".nes") \
       or file.endswith(".map") \
       or file.endswith(".lab") \
       or file.endswith(".nsfe") \
       :
        path = os.path.join(outdir, file)
        try:
            os.remove(path)
        except:
            errmsg("Unable to remove temporary file: " + path)

def flush_temp_tracks():
    global current_tracks_temp, nsf_tracks, nsf_albums, nsf_looptime_mins, nsf_looptime_secs
    if len(nsf_albums) == 0:
        return
    album_idx = len(nsf_albums) - 1
    for num in sorted(current_tracks_temp.keys()):
        data = current_tracks_temp[num]
        time_str = data.get('time', '00:00')
        if time_str == 'LOOP':
            mins, secs = nsf_looptime_mins, nsf_looptime_secs
        elif ':' in time_str:
            parts = time_str.split(':')
            mins, secs = int(parts[0]), int(parts[1])
        else:
            mins = 0
            secs = int(time_str) if time_str.isdigit() else 0
        
        title = data.get('title', f'Track {num}')
        copy = data.get('copy', '')
        nsf_tracks.append((title, num - 1, mins, secs, album_idx, copy))
    current_tracks_temp = {}

try:
    album_lines = open(album, "rt").readlines()
except:
    errmsg("Unable to read album file: " + album)

for i in range(len(album_lines)):
    def line_error(msg):
        errmsg(("Line %d: " % (i+1)) + msg)
    l = album_lines[i]
    l = strip_comment(l)
    l = l.rstrip()
    tokens = safe_tokens(l)
    if len(tokens) < 1:
        continue
    c = tokens[0]

    if c == "NSF":
        flush_temp_tracks()
        nsf_file = l[l.find(c) + len(c) + 1:].strip()
        nsf_albums.append({"file": nsf_file, "artist": "Unknown Artist"})
    elif c == "ARTIST":
        artist_val = l[l.find(c) + len(c) + 1:].strip()
        if len(nsf_albums) > 0:
            nsf_albums[-1]["artist"] = artist_val
        nsf_artist = artist_val
    elif c == "REGION":
        # Overrides a mislabeled NSF header's declared region (common on
        # ripped/homebrew files where the region byte was never set
        # correctly) for the purpose of the PLAY-rate throttle below --
        # doesn't touch the tune's own dual-region flag or its INIT call.
        if len(tokens) != 2 or tokens[1].upper() not in ("NTSC", "PAL"):
            line_error("REGION expects NTSC or PAL.")
        if len(nsf_albums) == 0:
            line_error("REGION specified before any NSF line.")
        nsf_albums[-1]["region_override"] = 0 if tokens[1].upper() == "NTSC" else 1
    elif c == "LOOPTIME":
        time_str = l[l.find(c) + len(c) + 1:].strip()
        if ':' in time_str:
            parts = time_str.split(':')
            nsf_looptime_mins, nsf_looptime_secs = int(parts[0]), int(parts[1])
        else:
            nsf_looptime_mins = 0
            nsf_looptime_secs = int(time_str)
    elif c == "LISTROWS":
        if len(tokens) != 2:
            line_error("LISTROWS expects one argument.")
        nsf_listrows = int(tokens[1])
    elif c == "AUTOFIX":
        if len(tokens) != 2:
            line_error("AUTOFIX expects one argument (0 or 1).")
        nsf_autofix = (tokens[1] != "0")
    elif c == "AUTONUMERATE":
        if len(tokens) != 2:
            line_error("AUTONUMERATE expects one argument (0 or 1).")
        nsf_autonumerate = (tokens[1] != "0")
    elif c == "TIME":
        if len(tokens) < 3:
            line_error("TIME expects track number and time argument.")
        tnum = int(tokens[1])
        time_str = tokens[2]
        if tnum not in current_tracks_temp:
            current_tracks_temp[tnum] = {}
        current_tracks_temp[tnum]['time'] = time_str
    elif c == "COPY":
        if len(tokens) < 2:
            line_error("COPY expects at least a track number.")
        tnum = int(tokens[1])
        # text argument is optional; empty copyright is allowed and just
        # results in nothing being drawn on the track screen
        copy_str = l[l.find(tokens[1]) + len(tokens[1]) + 1:].strip() if len(tokens) >= 3 else ""
        if tnum not in current_tracks_temp:
            current_tracks_temp[tnum] = {}
        current_tracks_temp[tnum]['copy'] = copy_str
    elif c == "TRACK":
        is_standard = False
        if len(tokens) >= 3:
            if ':' in tokens[1] or (tokens[1].isdigit() and tokens[2].isdigit()):
                is_standard = True

        if is_standard:
            time_arg = tokens[1]
            tnum_arg = tokens[2]
            title = l[l.find(tnum_arg, l.find(time_arg) + len(time_arg)) + len(tnum_arg) + 1:].strip()
            time_mins = 0
            time_secs = 0
            if ':' in time_arg:
                parts = time_arg.split(':')
                time_mins, time_secs = int(parts[0]), int(parts[1])
            else:
                time_secs = int(time_arg)
            if len(nsf_albums) == 0:
                line_error("TRACK specified before any NSF line.")
            num = int(tnum_arg)
            if num < 1:
                line_error("TRACK song number may not be less than 1.")
            nsf_tracks.append((title, num - 1, time_mins, time_secs, len(nsf_albums) - 1, ''))
        else:
            if len(tokens) < 2:
                line_error("TRACK expects track number or time/song arguments.")
            tnum = int(tokens[1])
            title = l[l.find(tokens[1]) + len(tokens[1]) + 1:].strip()
            if tnum not in current_tracks_temp:
                current_tracks_temp[tnum] = {}
            current_tracks_temp[tnum]['title'] = title
    elif c == "NROM":
        if len(tokens) != 2:
            line_error("NROM expects one argument.")
        if tokens[1] != "0" and tokens[1] != "1":
            line_error("NROM expects 0 or 1.")
        nsf_nrom = int(tokens[1])
    elif c == "TITLE":
        nsf_title = l[l.find(c) + len(c) + 1:].strip()
    elif c == "COPYRIGHT":
        nsf_copyright = l[l.find(c) + len(c) + 1:].strip()
    elif c == "PLAYALL":
        nsf_playall_text = l[l.find(c) + len(c) + 1:].strip()
    elif c == "INFOTEXT":
        nsf_info_text = l[l.find(c) + len(c) + 1:].strip()
    elif c == "SCREEN":
        if len(tokens) != 7:
            line_error("SCREEN expects 6 arguments.")
        nsf_screens.append((tokens[1], tokens[2], tokens[3], tokens[4], tokens[5], tokens[6]))
    elif c == "INFO":
        nsf_info.append(l[l.find(c) + len(c) + 1:])
    elif c == "COORD":
        if len(tokens) != 4:
            line_error("COORD expects 3 arguments.")
        coord = tokens[1]
        try:
            coord_x = int(tokens[2])
            coord_y = int(tokens[3])
            nsf_coord.append((coord, coord_x, coord_y))
        except:
            line_error("Unable to read number argument for COORD.")
    elif c == "CONST":
        if len(tokens) != 3:
            line_error("CONST expects 2 arguments.")
        ct = tokens[1]
        try:
            cv = int(tokens[2])
            nsf_const.append((ct, cv))
        except:
            line_error("Unable to read number argument for CONST.")
    else:
        line_error("Unknown statement type.")

flush_temp_tracks()

if len(nsf_albums) == 0:
    errmsg("No NSF file specified in album file.")
if nsf_nrom != 0 and len(nsf_albums) > 1:
    errmsg("NROM mode only supports a single NSF file. Use NROM 0 for multiple NSF files.")

if not nsf_artist and len(nsf_albums) > 0:
    nsf_artist = nsf_albums[0].get("artist", "Unknown Artist")

print("album info:")
for idx in range(len(nsf_albums)):
    print("  NSF #%d: %s (artist: %s)" % (idx, nsf_albums[idx]["file"], nsf_albums[idx].get("artist", "Unknown")))
print("  title: " + nsf_title)
print("  artist: " + nsf_artist)
print("  copyright: " + nsf_copyright)
print("  tracks: %d" % len(nsf_tracks))
print("  screens: %d" % len(nsf_screens))
print("  info lines: %d" % len(nsf_info))
print("  coordinates: %d" % len(nsf_coord))
print("  constants: %d" % len(nsf_const))
print()

nsf_banks = 0
nsf_dropped = []  # (index, file, reason) for albums AUTOFIX couldn't safely fix
nsf_status_report = []  # (original_file, status, detail) for every NSF, in order
MAX_TOTAL_BANKS = 256   # mapper 31's hard limit: $5FF8-$5FFF are single bytes

def estimate_banks(nsf_file, header):
    """Cheap, header-only replica of the highest_bank computation done
    below, used for a fast pre-check -- reads no more than 0x80 bytes."""
    banked = any(b != 0 for b in header[0x70:0x78])
    if not banked:
        return 7  # non-banked NSFs always occupy all 8 windows, see below
    return max(header[0x70:0x78])

if nsf_nrom == 0:
    # Fail fast on a plainly-too-large album *before* spending any time on
    # AUTOFIX's per-NSF emulation pass -- there's no point spending minutes
    # patching NSFs one by one only to find out ca65 can't fit them anyway.
    # A margin is reserved for the PPU graphics banks and the at-least-one
    # menu/idle bank added later, once graphics are compressed and sized.
    running_bank_total = 0
    for alb in nsf_albums:
        try:
            header = open(alb["file"], "rb").read(0x80)
        except:
            continue  # a missing/unreadable file is reported properly below
        if len(header) < 0x80:
            continue
        running_bank_total += estimate_banks(alb["file"], header) + 1
        if running_bank_total > MAX_TOTAL_BANKS - 8:
            errmsg(
                "Total ROM size is too large for this mapper after adding '%s': "
                "roughly %d x 4K banks needed so far, only %d are available in total. "
                "Remove some NSF files, use shorter/simpler ones, or split the album "
                "into more than one ROM." % (alb["file"], running_bank_total, MAX_TOTAL_BANKS))

for orig_idx, alb in enumerate(nsf_albums):
    nsf_file = alb["file"]
    try:
        nsf = open(nsf_file, "rb").read()
    except:
        errmsg("Unable to read NSF file: " + nsf_file)

    if len(nsf) < 0x80:
        errmsg("NSF file too small: " + nsf_file)

    bank = [0, 0, 0, 0, 0, 0, 0, 0]
    load_addr = nsf[0x08] + (nsf[0x09] << 8)
    init_addr = nsf[0x0A] + (nsf[0x0B] << 8)
    play_addr = nsf[0x0C] + (nsf[0x0D] << 8)
    region = nsf[0x7A]
    speed_ntsc = nsf[0x6E] + (nsf[0x6F] << 8)
    speed_pal = nsf[0x78] + (nsf[0x79] << 8)
    # The rate this tune actually expects PLAY to be called at, in Hz.
    # Only meaningful for non-dual-region NSFs (see rate_table below) --
    # a dual-region-aware tune is expected to compensate for the real
    # detected hardware rate internally instead.
    #
    # Deliberately NOT derived from the header's own speed_ntsc/speed_pal
    # fields: those are frequently garbage for whichever region a
    # single-region NSF *wasn't* authored for (nothing ever reads them,
    # so nothing ever catches them being wrong), and the builder GUI
    # always writes an explicit REGION now anyway. A plain, predictable
    # 60 or 50 keeps the throttle's behavior exactly matching whatever
    # NTSC/PAL choice was actually made in album.txt/the GUI.
    region_override = alb.get("region_override")
    effective_pal = region_override if region_override is not None else (region & 1)
    target_hz = 50 if effective_pal else 60
    banked = False

    for i in range(8):
        b = nsf[0x70 + i]
        bank[i] = b
        if b != 0:
            banked = True

    if not banked and load_addr < 0x8000:
        errmsg("NSF LOAD address below $8000. WRAM or FDS not supported: " + nsf_file)

    rom_padding = 0

    if banked:
        rom_padding = load_addr & 0x0FFF
        if nsf_nrom != 0:
            errmsg("NSF requires bankswitching, cannot be used with NROM: " + nsf_file)
    else:
        rom_padding = load_addr - 0x8000
        for i in range(8):
            bank[i] = i

    highest_bank = 0
    for i in range(8):
        if bank[i] > highest_bank:
            highest_bank = bank[i]

    f000_local = bank[7]
    rom = bytearray([0] * rom_padding) + nsf[0x80:]

    # Detect (not measure precisely -- see nsf_patch.measure_pal_compensation's
    # own notes on why only the yes/no answer is trusted) whether this
    # tune's own engine self-compensates tempo based on the real region
    # passed via X at INIT, independent of anything this driver does.
    # FamiTracker's compiled driver applies exactly 6/5 when it does this,
    # regardless of the specific tune -- so once compensation is detected
    # at all, that known-exact constant is used rather than the noisy
    # measured ratio.
    try:
        import nsf_patch
        compensates = nsf_patch.measure_pal_compensation(
            rom, bank, init_addr, play_addr, nsf[0x06]) > 1.0
    except ImportError:
        compensates = False

    alb.update({
        "nsf": nsf, "bank": bank, "banked": banked,
        "load_addr": load_addr, "init_addr": init_addr, "play_addr": play_addr,
        "region": region, "rom_padding": rom_padding, "highest_bank": highest_bank,
        "f000_local": f000_local, "rom": rom, "song_count": nsf[0x06],
        "target_hz": target_hz,
        "effective_pal": effective_pal,
        "compensates": compensates,
    })

    if nsf_autofix:
        try:
            import nsf_patch
        except ImportError:
            errmsg("AUTOFIX requires the 'py65' package (pip install py65 --break-system-packages).")
        print("  AUTOFIX: checking for reserved-memory conflicts ($00FA-$00FF, $0400-$07FF)...")
        patched, fix_report = nsf_patch.autofix_rom(
            alb["rom"], alb["bank"], alb["init_addr"], alb["play_addr"],
            alb["region"], alb["song_count"])
        if patched is not None:
            alb["rom"] = patched
            # Keep patched copies together in one place, under the original
            # name + ".patched", so they're easy to find and reuse later
            # instead of scattering generated files next to the sources
            # (which may live anywhere -- see the multi-location NSF fix).
            patched_dir = os.path.join(os.path.dirname(os.path.abspath(album)), "_patched_nsfs")
            try:
                os.makedirs(patched_dir, exist_ok=True)
            except OSError:
                pass
            base, ext = os.path.splitext(os.path.basename(nsf_file))
            fixed_path = os.path.join(patched_dir, base + ".patched" + ext)
            new_prg = bytes(patched[rom_padding: rom_padding + (len(nsf) - 0x80)])
            try:
                with open(fixed_path, "wb") as ff:
                    ff.write(nsf[:0x80])
                    ff.write(new_prg)
                alb["file"] = fixed_path
                print("  AUTOFIX: patched (%s) -- %d direct reference(s) relocated; wrote %s"
                      % (fix_report["status"], fix_report["direct_hits"] - fix_report["skipped"], fixed_path))
                nsf_status_report.append((nsf_file, "patched", fixed_path))
            except Exception as e:
                print("  AUTOFIX: patched in memory but could not write %s: %s" % (fixed_path, e))
                nsf_status_report.append((nsf_file, "patched", "in memory only, could not write copy: %s" % e))
            if fix_report["indirect_hits"]:
                print("  AUTOFIX: %d indirect (pointer-based) reference(s) could NOT be "
                      "auto-patched -- this NSF may still misbehave" % fix_report["indirect_hits"])
        else:
            print("  AUTOFIX: %s (%s)" % (fix_report["status"], fix_report["detail"]))
            if fix_report["status"] in ("failed", "error"):
                print("  AUTOFIX: dropping this NSF and its tracks from the build.")
                nsf_dropped.append((orig_idx, nsf_file, fix_report["detail"]))
                nsf_status_report.append((nsf_file, "skipped", fix_report["detail"]))
                continue  # do not print the NSF summary below for a dropped album
            nsf_status_report.append((nsf_file, "unchanged", fix_report["detail"]))
    else:
        nsf_status_report.append((nsf_file, "unchanged", "AUTOFIX disabled"))

    print("NSF (%s):" % nsf_file)
    print("  LOAD: %04X" % load_addr)
    print("  INIT: %04X" % init_addr)
    print("  PLAY: %04X" % play_addr)
    print("  ROM size: %d bytes" % len(rom))
    if region & 2:
        print("  REGION: dual (tune compensates for real hardware region itself)")
    else:
        override_note = " (overridden by REGION directive)" if region_override is not None else ""
        print("  REGION: %s, PLAY throttled to %d Hz%s"
              % ("PAL" if effective_pal else "NTSC", target_hz, override_note))
        if compensates:
            print("  REGION: engine self-compensates ~1.20x for real PAL hardware "
                  "-- call rate on real PAL adjusted to %d Hz to cancel it out"
                  % max(1, round(target_hz * 5.0 / 6.0)))
print()

if nsf_dropped:
    dropped_indices = set(i for (i, f, r) in nsf_dropped)
    kept_albums = [alb for i, alb in enumerate(nsf_albums) if i not in dropped_indices]
    index_map = {}
    new_i = 0
    for old_i in range(len(nsf_albums)):
        if old_i in dropped_indices:
            continue
        index_map[old_i] = new_i
        new_i += 1
    nsf_tracks[:] = [
        (t, n, m, s, index_map[a], c) for (t, n, m, s, a, c) in nsf_tracks if a in index_map
    ]
    nsf_albums[:] = kept_albums
    if len(nsf_albums) == 0:
        errmsg("Every NSF file was skipped (AUTOFIX could not make any of them safe to combine "
               "with the driver) -- nothing left to build.")

# Machine-readable block a GUI can parse for a clean summary, instead of
# scraping the human-oriented log above.
print("==NSF_STATUS==")
for fname, status, detail in nsf_status_report:
    safe_detail = str(detail).replace("|", "/").replace("\n", " ")
    print("%s|%s|%s" % (fname, status, safe_detail))
print("==END_NSF_STATUS==")
print()

def output_banks(prefix, data, trim=-1, minbanks=0):
    banks = 0
    extent = max(len(data), minbanks * 0x1000)
    while (banks * 0x1000) < extent:
        bank = banks
        of = os.path.join(outdir, prefix + ("%02X.bin" % bank))
        try:
            offset = bank * 0x1000
            s = data[offset: offset + 0x1000]
            if len(s) < 0x1000:
                s += bytearray([0] * (0x1000 - len(s)))
            if bank == trim:
                s = s[0:len(s) - 6]
            open(of, "wb").write(s)
            print("Output: " + of)
        except:
            errmsg("Unable to write file: " + of)
        banks += 1
    return banks

def find_free_run(rom, chunk, min_len, avoid_tail_chunk=None):
    chunk_start = (chunk // 0x1000) * 0x1000
    chunk_bytes = rom[chunk_start:chunk_start + 0x1000]
    usable_len = 0x1000
    if avoid_tail_chunk is not None and (chunk_start // 0x1000) == avoid_tail_chunk:
        usable_len = 0x1000 - 6
    run = 0
    run_start = 0
    for i in range(usable_len):
        if chunk_bytes[i] == 0:
            if run == 0:
                run_start = i
            run += 1
            if run >= min_len:
                return chunk_start + run_start
        else:
            run = 0
    return None

def patch_dynamic_bankswitch(rom, bank_table, offset, label, f000_local=None):
    if offset == 0:
        return rom, 0, 0
    rom = bytearray(rom)
    n = len(rom)
    reverse_map = {}
    for win in range(8):
        reverse_map[bank_table[win]] = 0x8000 + win * 0x1000
    def file_to_cpu(off):
        chunk = off // 0x1000
        within = off % 0x1000
        if chunk in reverse_map:
            return reverse_map[chunk] + within
        return None
    def find_callsites(target_addr):
        lo = target_addr & 0xFF
        hi = (target_addr >> 8) & 0xFF
        return [i for i in range(n - 2) if rom[i] == 0x20 and rom[i + 1] == lo and rom[i + 2] == hi]

    routB = []
    for p in range(n - 14):
        if (rom[p] == 0x18 and rom[p + 1] == 0x8D and 0xF8 <= rom[p + 2] <= 0xFD and rom[p + 3] == 0x5F
            and rom[p + 4] == 0x69 and rom[p + 5] == 0x01
            and rom[p + 6] == 0x8D and rom[p + 7] == rom[p + 2] + 1 and rom[p + 8] == 0x5F
            and rom[p + 9] == 0x69 and rom[p + 10] == 0x01
            and rom[p + 11] == 0x8D and rom[p + 12] == rom[p + 2] + 2 and rom[p + 13] == 0x5F
            and rom[p + 14] == 0x60):
            routB.append(p)
    def overlaps_routB(p):
        return any(b <= p < b + 15 for b in routB)
    routA = [p for p in range(n - 3)
             if rom[p] == 0x8D and 0xF8 <= rom[p + 1] <= 0xFF and rom[p + 2] == 0x5F and rom[p + 3] == 0x60
             and not overlaps_routB(p)]

    countA = 0
    countB = 0

    for p in routA:
        cpu_addr = file_to_cpu(p)
        if cpu_addr is None:
            continue
        reg = rom[p + 1]
        free = find_free_run(rom, p, 8, f000_local)
        if free is None:
            errmsg("%s: no free space found to patch an in-song bankswitch routine." % label)
        new_code = bytes([0x18, 0x69, offset & 0xFF, 0x8D, reg, 0x5F, 0x60])
        rom[free:free + len(new_code)] = new_code
        new_cpu = file_to_cpu(free)
        callsites = find_callsites(cpu_addr)
        for cs in callsites:
            rom[cs + 1] = new_cpu & 0xFF
            rom[cs + 2] = (new_cpu >> 8) & 0xFF
        print("  %s: patched in-song bank routine at $%04X (single window $5F%02X), "
              "%d call site(s), offset +%d" % (label, cpu_addr, reg, len(callsites), offset))
        countA += 1

    for p in routB:
        cpu_addr = file_to_cpu(p)
        if cpu_addr is None:
            continue
        reg1 = rom[p + 2]
        free = find_free_run(rom, p, 18, f000_local)
        if free is None:
            errmsg("%s: no free space found to patch an in-song bankswitch routine." % label)
        new_code = bytes([0x18, 0x69, offset & 0xFF,
                           0x8D, reg1, 0x5F,
                           0x69, 0x01,
                           0x8D, reg1 + 1, 0x5F,
                           0x69, 0x01,
                           0x8D, reg1 + 2, 0x5F,
                           0x60])
        rom[free:free + len(new_code)] = new_code
        new_cpu = file_to_cpu(free)
        callsites = find_callsites(cpu_addr)
        for cs in callsites:
            rom[cs + 1] = new_cpu & 0xFF
            rom[cs + 2] = (new_cpu >> 8) & 0xFF
        print("  %s: patched in-song bank routine at $%04X (three windows starting $5F%02X), "
              "%d call site(s), offset +%d" % (label, cpu_addr, reg1, len(callsites), offset))
        countB += 1

    if countA == 0 and countB == 0:
        print("  %s: no in-song dynamic bankswitch routines found (nothing to patch)" % label)

    return rom, countA, countB

if nsf_nrom != 0:
    alb = nsf_albums[0]
    of = os.path.join(outdir, "nsf_nrom.bin")
    try:
        open(of, "wb").write(alb["rom"])
        print("Output: " + of)
    except:
        errmsg("Unable to write file: " + of)
else:
    total_banks = 0
    for idx in range(len(nsf_albums)):
        alb = nsf_albums[idx]
        base = total_banks
        alb["base"] = base
        if base != 0:
            alb["rom"], patchedA, patchedB = patch_dynamic_bankswitch(
                alb["rom"], alb["bank"], base, "NSF #%d (%s)" % (idx, alb["file"]), alb["f000_local"])
        banks_out = output_banks("nsf%d_" % idx, alb["rom"], alb["f000_local"], alb["highest_bank"] + 1)
        alb["banks_out"] = banks_out
        alb["bank_global"] = [base + b for b in alb["bank"]]
        alb["f000_global"] = base + alb["f000_local"]
        total_banks += banks_out
        print("NSF #%d 4k banks: %d (physical base %d)" % (idx, banks_out, alb["base"]))
    nsf_banks = total_banks
    print("Total NSF 4k banks: %d" % nsf_banks)
print()

def unpack_ppu(rle):
    data = bytearray()
    run = 0
    command = 0
    for b in rle:
        if command == 0:
            if b == 0:
                command = 1
            else:
                run = b
                command = 3
        elif command == 1:
            if b == 0:
                command = 4
                break
            else:
                run = b
                command = 2
        elif command == 2:
            data = data + bytearray([b] * run)
            command = 0
        elif command == 3:
            data.append(b)
            run -= 1
            if run < 1:
                command = 0
        else:
            errmsg("Internal problem in RLE verification.")
    if command != 4:
        errmsg("RLE end of stream reached without marker.")
    return data

def pack_ppu(data):
    def emit_compressed(s):
        output = bytearray()
        for sb in s:
            if sb != s[0]:
                errmsg("Internal problem in RLE compressor!")
        while len(s) > 0:
            emit = min(len(s), 255)
            output.append(0)
            output.append(emit)
            output.append(s[0])
            s = s[emit:]
        return output
    def emit_uncompressed(s):
        output = bytearray()
        while len(s) > 0:
            emit = min(len(s), 255)
            output.append(emit)
            output += s[0:emit]
            s = s[emit:]
        return output

    rle = bytearray()
    run = bytearray()
    running = False
    for d in data:
        run.append(d)
        rl = len(run)
        if running:
            if d != run[rl - 2]:
                rle += emit_compressed(run[0:rl - 1])
                run = run[rl - 1:]
                running = False
        else:
            if rl >= 4:
                if d == run[rl - 2] and d == run[rl - 3] and d == run[rl - 4]:
                    rle += emit_uncompressed(run[0:rl - 4])
                    run = run[rl - 4:]
                    running = True
    if running:
        rle += emit_compressed(run)
    else:
        rle += emit_uncompressed(run)
    rle.append(0)
    rle.append(0)

    unpacked = unpack_ppu(rle)
    if len(unpacked) != len(data):
        errmsg("RLE packing verification failed; length mismatch.")
    for i in range(0, len(data)):
        if unpacked[i] != data[i]:
            errmsg("RLE packing verification failed.")
    return rle

ppu_files = {}
ppu_offsets = {}
ppu_data = bytearray()
nrom_chr0 = ""
nrom_chr1 = ""

print("PPU data compression:")
for s in nsf_screens:
    for i in range(1, 6):
        f = s[i]
        if nsf_nrom != 0:
            if i == 2:
                if nrom_chr0 == "":
                    nrom_chr0 = f
                elif nrom_chr0 != f:
                    errmsg("All NROM screens must use the same two CHR pages.")
            elif i == 3:
                if nrom_chr1 == "":
                    nrom_chr1 = f
                elif nrom_chr1 != f:
                    errmsg("All NROM screens must use the same two CHR pages.")
        if f in ppu_files:
            continue
        ppu_files[f] = len(ppu_files)

ppu_offset = 0
ppu_files_immutable = sorted(ppu_files.items())
for (f, i) in ppu_files_immutable:
    try:
        data = open(f, "rb").read()
    except:
        errmsg("Unable to read file: " + f)
    packed = pack_ppu(data)
    ppu_offsets[f] = ppu_offset
    if (nrom_chr0 == f) or (nrom_chr1 == f):
        packed = bytearray([0, 0])
    ppu_offset += len(packed)
    ppu_data += packed
    print("Compressed: %-20s from %4d / %4d bytes" % (f, len(packed), len(data)))

if nsf_nrom != 0:
    of = os.path.join(outdir, "ppu_nrom.bin")
    try:
        open(of, "wb").write(ppu_data)
        print("Output: " + of)
    except:
        errmsg("Unable to write file: " + of)
else:
    ppu_banks = output_banks("ppu_", ppu_data)
    print("PPU 4k banks: %d" % ppu_banks)
    if ppu_banks > 7:
        errmsg("Too many PPU banks! Maximum: 7")
print()

if nsf_nrom == 0:
    needed_banks = nsf_banks + ppu_banks + 1
    padded_banks = 1
    while padded_banks < needed_banks:
        padded_banks *= 2
else:
    padded_banks = int(32 / 4)

def ppu_file_enum(s):
    so = ""
    for c in s:
        so += c if (('a' <= c <= 'z') or ('A' <= c <= 'Z')) else "_"
    return so

s = ""
s += "; automatically generated by eznsf.py\n"
s += "; " + now_string + "\n\n"
s += "MAPPER = %d\n" % (0 if (nsf_nrom != 0) else 31)
s += "BANKS = %d ; 4k bank count\n" % padded_banks
if nsf_nrom != 0:
    alb = nsf_albums[0]
    s += ".define NROM_CHR0 \"%s\"\n" % nrom_chr0
    s += ".define NROM_CHR1 \"%s\"\n" % nrom_chr1
    s += ".define PPU_NROM_BIN \"%s/ppu_nrom.bin\"\n" % outdir
    s += ".define NSF_NROM_BIN \"%s/nsf_nrom.bin\"\n" % outdir
else:
    alb0 = nsf_albums[0]
    s += ".define NSF_F000 \"%s/nsf0_%02X.bin\"\n" % (outdir, alb0["f000_local"])
s += "\n.enum eNSF\n"
if nsf_nrom != 0:
    alb = nsf_albums[0]
    region_or = alb["region"]
else:
    region_or = 0
    for alb in nsf_albums:
        region_or |= alb["region"]
s += "\tREGION = %d\n" % region_or
s += "\tTRACKS = %d\n" % len(nsf_tracks)
s += "\tALBUMS = %d\n" % len(nsf_albums)
if nsf_nrom == 0:
    s += "\tBANKS = $%02X\n" % nsf_banks
s += "\tLISTROWS = %d\n" % nsf_listrows
s += ".endenum\n\n"

s += ".enum eScreen\n"
for i in range(len(nsf_screens)):
    s += "\t%-20s = %2d\n" % (nsf_screens[i][0], i)
s += ".endenum\n\n"

s += ".enum ePPU\n"
i = 0
for (k, v) in ppu_files_immutable:
    s += "\t%-20s = %2d\n" % (ppu_file_enum(k), i)
    i += 1
s += ".endenum\n\n"

s += ".enum eCoord\n"
for coord in nsf_coord:
    s += "\t%-30s = %d\n" % (coord[0] + "_X", coord[1])
    s += "\t%-30s = %d\n" % (coord[0] + "_Y", coord[2])
s += ".endenum\n\n"

s += ".enum eConst\n"
for c in nsf_const:
    s += "\t%-30s = %d\n" % (c[0], c[1])
s += ".endenum\n\n; end of file\n"

of = os.path.join(outdir, "enums.sh")
try:
    open(of, "wt").write(s)
    print("Output: " + of)
except:
    errmsg("Unable to write file: " + of)

s = ""
s += "; automatically generated by eznsf.py\n"
s += "; " + now_string + "\n\n"
s += ".scope dString\n"
s += "\ttitle:     .asciiz \"" + nsf_title     + "\"\n"
s += "\tartist:    .asciiz \"" + nsf_artist    + "\"\n"
s += "\tcopyright: .asciiz \"" + nsf_copyright + "\"\n"
for idx in range(len(nsf_albums)):
    s += "\tartist_%02d: .asciiz \"%s\"\n" % (idx, nsf_albums[idx].get("artist", nsf_artist))
for i in range(len(nsf_tracks)):
    s += "\ttrack_%02d:  .asciiz \"%s\"\n" % (i, nsf_tracks[i][0])
for i in range(len(nsf_tracks)):
    # Separate from track_%02d on purpose: AUTONUMERATE should only prefix
    # the tracklist screen, not the now-playing screen, so each needs its
    # own string even though they're identical when AUTONUMERATE is off.
    list_title = ("%02d. %s" % (i + 1, nsf_tracks[i][0])) if nsf_autonumerate else nsf_tracks[i][0]
    s += "\tlist_track_%02d:  .asciiz \"%s\"\n" % (i, list_title)
for i in range(len(nsf_tracks)):
    s += "\tcopy_%02d:   .asciiz \"%s\"\n" % (i, nsf_tracks[i][5])
s += "\tlist_play_all: .asciiz \"%s\"\n" % nsf_playall_text
s += "\tlist_info:     .asciiz \"%s\"\n" % nsf_info_text
s += "\tinfo:\n"
for si in nsf_info:
    s += "\t\t.byte \"%s\",13\n" % si
s += "\t\t.byte 0\n"
s += ".endscope\n\n"

s += ".scope dNSF\n"
s += "\tbank_table:\n"
for idx in range(len(nsf_albums)):
    alb = nsf_albums[idx]
    bt = alb["bank"] if nsf_nrom != 0 else alb["bank_global"]
    s += "\t\t.byte " + ", ".join("$%02X" % b for b in bt) + (" ; album %d\n" % idx)
s += "\tinit_table:\n"
for idx in range(len(nsf_albums)):
    s += "\t\t.word $%04X\n" % nsf_albums[idx]["init_addr"]
s += "\tplay_table:\n"
for idx in range(len(nsf_albums)):
    s += "\t\t.word $%04X\n" % nsf_albums[idx]["play_addr"]
s += "\tbankf000_table:\n"
for idx in range(len(nsf_albums)):
    f = nsf_albums[idx]["f000_local"] if nsf_nrom != 0 else nsf_albums[idx]["f000_global"]
    s += "\t\t.byte $%02X\n" % f
s += "\tartist_table:\n"
for idx in range(len(nsf_albums)):
    s += "\t\t.addr dString::artist_%02d\n" % idx
s += "\trate_table:\n"
for idx in range(len(nsf_albums)):
    s += "\t\t.byte %d ; Hz this album's tune actually expects PLAY to run at\n" % nsf_albums[idx]["target_hz"]
s += "\trate_table_pal_adjusted:\n"
for idx in range(len(nsf_albums)):
    # Used instead of rate_table when the real hardware turns out to be
    # PAL (see play_init) for a tune detected to self-compensate tempo
    # via X (see "compensates" above): pre-dividing our own call-rate
    # target by that same known 6/5 cancels the tune's own multiplication
    # back out, so the combined *result* lands on the declared target
    # instead of overshooting it. Identical to rate_table for anything
    # not detected to compensate, so this never second-guesses a tune
    # that doesn't need it -- play_play's rate converter can hit either
    # table exactly regardless of the real native rate either way.
    if nsf_albums[idx].get("compensates"):
        s += "\t\t.byte %d\n" % max(1, round(nsf_albums[idx]["target_hz"] * 5.0 / 6.0))
    else:
        s += "\t\t.byte %d\n" % nsf_albums[idx]["target_hz"]
s += "\tdual_table:\n"
for idx in range(len(nsf_albums)):
    s += "\t\t.byte %d ; 1 = tune claims to compensate for region itself, no throttling needed\n" % (1 if (nsf_albums[idx]["region"] & 2) else 0)
s += ".endscope\n\n"

s += ".scope dTrack\n"
s += "\tstring_table:\n"
for i in range(len(nsf_tracks)):
    s += "\t\t.addr dString::track_%02d\n" % i
s += "\tcopy_table:\n"
for i in range(len(nsf_tracks)):
    s += "\t\t.addr dString::copy_%02d\n" % i
s += "\tsong_table:\n"
for i in range(len(nsf_tracks)):
    s += "\t\t.byte %3d\n" % nsf_tracks[i][1]
s += "\tlength_table:\n"
for i in range(len(nsf_tracks)):
    s += "\t\t.word (%2d * 60) + %2d\n" % (nsf_tracks[i][2], nsf_tracks[i][3])
s += "\talbum_table:\n"
for i in range(len(nsf_tracks)):
    s += "\t\t.byte %d\n" % nsf_tracks[i][4]
s += ".endscope\n\n"

s += ".enum eList\n"
s += "\tENTRIES = %d\n" % (len(nsf_tracks) + 2)
s += ".endenum\n\n"

s += ".scope dList\n"
s += "\tstring_table:\n"
s += "\t\t.addr dString::list_play_all\n"
for i in range(len(nsf_tracks)):
    s += "\t\t.addr dString::list_track_%02d\n" % i
s += "\t\t.addr dString::list_info\n"
s += ".endscope\n\n"

s += ".scope dScreen\n"
s += "\tname_table:\n"
for i in range(len(nsf_screens)):
    s += "\t\t.byte ePPU::%-25s\n" % ppu_file_enum(nsf_screens[i][1])
s += "\tchr0_table:\n"
for i in range(len(nsf_screens)):
    s += "\t\t.byte ePPU::%-25s\n" % ppu_file_enum(nsf_screens[i][2])
s += "\tchr1_table:\n"
for i in range(len(nsf_screens)):
    s += "\t\t.byte ePPU::%-25s\n" % ppu_file_enum(nsf_screens[i][3])
s += "\tpal0_table:\n"
for i in range(len(nsf_screens)):
    s += "\t\t.byte ePPU::%-25s\n" % ppu_file_enum(nsf_screens[i][4])
s += "\tpal1_table:\n"
for i in range(len(nsf_screens)):
    s += "\t\t.byte ePPU::%-25s\n" % ppu_file_enum(nsf_screens[i][5])
s += ".endscope\n\n"

s += ".scope dPPU\n"
s += "\tdata_table:\n"
i = 0
for (k, v) in ppu_files_immutable:
    s += "\t\t.addr data_base + $%04X\n" % ppu_offsets[k]
    i += 1
s += ".endscope\n\n; end of file\n"

of = os.path.join(outdir, "tables.sh")
try:
    open(of, "wt").write(s)
    print("Output: " + of)
except:
    errmsg("Unable to write file: " + of)
print()

def execute(args):
    print("Run: " + " ".join(args))
    print()
    proc = subprocess.Popen(args, stderr=subprocess.STDOUT, stdout=subprocess.PIPE)
    proc.wait()
    for l in proc.stdout:
        print(l.decode().rstrip())
    return proc.returncode

link_object = os.path.join(outdir, "eznsf.o")

if 0 != execute([ca65, "eznsf.s", "-I", outdir, "-g", "-o", link_object]):
    print()
    errmsg("Assemble of eznsf.s has failed!")

ld65_debug = ["-m", os.path.join(outdir, "eznsf.map"), "-Ln", os.path.join(outdir, "eznsf.lab")]
if nsf_nrom != 0:
    if 0 != execute([ld65, "-o", os.path.join(outdir, "eznsf.nes"), "-C", "eznsf_nrom.cfg"] + ld65_debug + [link_object]):
        print()
        errmsg("Link of eznsf.nes has failed!")
else:
    if 0 != execute([ld65, "-o", os.path.join(outdir, "eznsf.bin"), "-C", "eznsf.cfg"] + ld65_debug + [link_object]):
        print()
        errmsg("Link of eznsf.bin has failed!")
    if 0 != execute([ld65, "-o", os.path.join(outdir, "f000.bin"), "-C", "eznsf_f000.cfg"] + [link_object]):
        print()
        errmsg("Link of f000.bin has failed!")
    if 0 != execute([ld65, "-o", os.path.join(outdir, "header.bin"), "-C", "eznsf_header.cfg"] + [link_object]):
        print()
        errmsg("Link of header.bin has failed!")

    nsf_albums[0]["f000_file"] = os.path.join(outdir, "f000.bin")
    try:
        vector_bytes = open(nsf_albums[0]["f000_file"], "rb").read()[-6:]
    except:
        errmsg("Unable to read file: " + nsf_albums[0]["f000_file"])
    for idx in range(1, len(nsf_albums)):
        alb = nsf_albums[idx]
        src = os.path.join(outdir, "nsf%d_%02X.bin" % (idx, alb["f000_local"]))
        try:
            data = open(src, "rb").read()
        except:
            errmsg("Unable to read file: " + src)
        if len(data) != 0x1000 - 6:
            errmsg("Unexpected size for trimmed NSF bank: " + src)
        of = os.path.join(outdir, "f000_%d.bin" % idx)
        try:
            open(of, "wb").write(data + vector_bytes)
            print("Output: " + of)
        except:
            errmsg("Unable to write file: " + of)
        alb["f000_file"] = of

    def readbin(f, seg):
        try:
            b = open(f, "rb").read()
        except:
            errmsg("Unable to read file: " + f)
        print("Segment %03X: %s" % (seg & 0xFFF, f))
        return b

    print("Concatenating %d banks..." % padded_banks)
    output_nes = bytearray()
    output_nes += readbin(os.path.join(outdir, "header.bin"), -1)
    seg = 0
    for idx in range(len(nsf_albums)):
        alb = nsf_albums[idx]
        for i in range(alb["banks_out"]):
            if i == alb["f000_local"]:
                output_nes += readbin(alb["f000_file"], seg)
            else:
                output_nes += readbin(os.path.join(outdir, "nsf%d_%02X.bin" % (idx, i)), seg)
            seg += 1
    for i in range(ppu_banks):
        output_nes += readbin(os.path.join(outdir, "ppu_%02X.bin" % i), seg)
        seg += 1
    for i in range(padded_banks - (nsf_banks + ppu_banks)):
        output_nes += readbin(os.path.join(outdir, "eznsf.bin"), seg)
        seg += 1

    try:
        of = os.path.join(outdir, "eznsf.nes")
        open(of, "wb").write(output_nes)
        print("Output: " + of)
    except:
        errmsg("Unable to write file: " + of)
    print()

if output_nsfe:
    def fourcc(s):
        fcc = bytearray()
        for i in range(4):
            fcc.append(ord(s[i]))
        return fcc

    def packword(w):
        w = w & 0xFFFF
        wb = bytearray()
        wb.append(w & 255)
        wb.append(w >> 8)
        return wb

    def packlong(l):
        l = l & 0xFFFFFFFF
        lb = bytearray()
        lb.append(l & 255)
        lb.append((l >> 8) & 255)
        lb.append((l >> 16) & 255)
        lb.append(l >> 24)
        return lb

    def packstring(s):
        sb = bytearray(s.encode(encoding="utf-8"))
        sb.append(0)
        return sb

    def nsfe_chunk(fcc, data):
        chunk = bytearray()
        chunk += packlong(len(data))
        chunk += fourcc(fcc)
        chunk += data
        return chunk

    for idx in range(len(nsf_albums)):
        alb = nsf_albums[idx]
        nsf = alb["nsf"]
        nsf_bank = alb["bank"]
        nsf_banked = alb["banked"]
        song_count = alb["song_count"]

        nsfe_tracks = {}
        for (t, n, m, s, a, c) in nsf_tracks:
            if a == idx:
                nsfe_tracks[n] = (t, m, s)

        nsfe_rom = bytearray()
        nsfe_rom += fourcc("NSFE")

        nsfe_info = bytearray()
        nsfe_info += packword(alb["load_addr"])
        nsfe_info += packword(alb["init_addr"])
        nsfe_info += packword(alb["play_addr"])
        nsfe_info.append(alb["region"])
        nsfe_info.append(nsf[0x7B])
        nsfe_info.append(song_count)
        nsfe_info.append(nsf[0x07] - 1)
        nsfe_rom += nsfe_chunk("INFO", nsfe_info)

        if nsf_banked:
            nsfe_rom += nsfe_chunk("BANK", bytearray(nsf_bank))

        nsfe_rom += nsfe_chunk("DATA", nsf[0x80:])

        nsfe_auth = bytearray()
        nsfe_auth += packstring(nsf_title)
        nsfe_auth += packstring(alb.get("artist", nsf_artist))
        nsfe_auth += packstring(nsf_copyright)
        nsfe_auth += packstring("eznsf.py")
        nsfe_rom += nsfe_chunk("auth", nsfe_auth)

        nsfe_plst = bytearray()
        for (t, n, m, s, a, c) in nsf_tracks:
            if a == idx:
                nsfe_plst.append(n)
        nsfe_rom += nsfe_chunk("plst", nsfe_plst)

        nsfe_time = bytearray()
        for ti in range(0, song_count):
            if ti in nsfe_tracks:
                (t, m, s) = nsfe_tracks[ti]
                nsfe_time += packlong(1000 * ((m * 60) + s))
            else:
                nsfe_time += packlong(-1)
        nsfe_rom += nsfe_chunk("time", nsfe_time)

        nsfe_tlbl = bytearray()
        for ti in range(0, song_count):
            if ti in nsfe_tracks:
                (t, m, s) = nsfe_tracks[ti]
                nsfe_tlbl += packstring(t)
            else:
                nsfe_tlbl.append(0)
        nsfe_tlbl.append(0)
        nsfe_rom += nsfe_chunk("tlbl", nsfe_tlbl)

        nsfe_rom += nsfe_chunk("text", packstring("eznsf.py"))

        nsfe_rom += nsfe_chunk("NEND", bytearray())

        if len(nsf_albums) > 1:
            of = os.path.join(outdir, "eznsf_%d.nsfe" % idx)
        else:
            of = os.path.join(outdir, "eznsf.nsfe")
        try:
            open(of, "wb").write(nsfe_rom)
            print("Output: " + of)
        except:
            errmsg("Unable to write file: " + of)
    print()

print("Success!")
sys.exit(0)

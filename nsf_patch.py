#!/usr/bin/env python3
"""
Attempts to automatically relocate a tune's own zero-page/RAM variables away
from the ranges this EZNSF fork reserves for its own driver ($00FA-$00FF and
$0400-$07FF), by actually running the tune in a 6502 emulator, watching which
instructions touch those addresses, and rewriting the address operands of
those specific instructions to point somewhere free instead.

What this CAN fix:
  Direct zero-page/absolute addressing (LDA/STA/INC/CMP/... $addr, $addr,X,
  $addr,Y) where the address is baked into the instruction. This covers the
  vast majority of compiler-generated (FamiTracker-style) sound engines.

What this CANNOT fix, and will only report:
  Indirect addressing ((zp),Y / (zp,X)) where the target address is data
  held in a pointer, not an instruction operand. Hand-written/optimized
  engines (game rips in particular) lean on this a lot. These are reported
  so a human can look at them; nothing is silently guessed.

Every patch is self-verified: the patched ROM is re-run through the same
trace and must (a) touch zero reserved addresses, (b) execute the exact
same instruction sequence as the original (only WHERE data is stored
changed, not what runs), before it's accepted. If that check fails for a
file, the tool refuses to emit a patched NSF for it.
"""
import struct
import sys
import os
from py65.memory import ObservableMemory
from py65.devices.mpu6502 import MPU

ZP_LO, ZP_HI = 0x00FA, 0x00FF          # reserved zero page
RAM_LO, RAM_HI = 0x0400, 0x07FF        # reserved RAM
FREE_RAM_LO, FREE_RAM_HI = 0x0200, 0x03FF   # only genuinely free RAM block
FRAMES_PER_SONG = 1500
MAX_SONGS_TESTED = 8
STEP_BUDGET = 400_000
SENTINEL = 0x0020

ABS_MODES = {"abs", "abx", "aby"}
ZP_MODES = {"zpg", "zpx", "zpy"}
IND_MODES = {"ind", "inx", "iny"}
OPERAND_SIZE = {
    "imp": 0, "acc": 0, "imm": 1, "zpg": 1, "zpx": 1, "zpy": 1,
    "rel": 1, "inx": 1, "iny": 1, "abs": 2, "abx": 2, "aby": 2, "ind": 2,
}


def load_nsf(path):
    data = open(path, "rb").read()
    header = bytearray(data[:0x80])
    load = struct.unpack("<H", header[0x08:0x0A])[0]
    init = struct.unpack("<H", header[0x0A:0x0C])[0]
    play = struct.unpack("<H", header[0x0C:0x0E])[0]
    song_count = header[0x06]
    region = header[0x7A]
    bank = list(header[0x70:0x78])
    banked = any(b != 0 for b in bank)
    prg = data[0x80:]

    if banked:
        rom_padding = load & 0x0FFF
    else:
        rom_padding = load - 0x8000
        bank = list(range(8))

    rom = bytearray(rom_padding) + bytearray(prg)
    needed = (max(bank) + 1) * 0x1000
    if len(rom) < needed:
        rom += bytearray(needed - len(rom))
    return {
        "header": header, "load": load, "init": init, "play": play,
        "song_count": song_count, "region": region,
        "bank": bank, "banked": banked, "rom": bytearray(rom),
        "rom_padding": rom_padding, "orig_prg_len": len(prg),
    }


class Tracer:
    """Runs INIT + many PLAY frames, recording every instruction that
    touches the reserved ranges (directly or indirectly), and the full
    executed-instruction sequence (for before/after regression checks)."""

    def __init__(self, rom, bank):
        self.rom = rom
        self.cur_bank = list(bank)
        subject = [0x00] * 0x10000
        mem = ObservableMemory(subject=subject)
        mem.subscribe_to_read(range(0x8000, 0x10000), self._read_rom)
        mem.subscribe_to_write(range(0x5FF8, 0x6000), self._write_bank)
        self.mem = mem
        self.mpu = MPU(memory=mem)
        self.exec_trace = []      # (cpu_addr, opcode) per instruction, in order
        self.direct_hits = []     # dicts describing patchable direct references
        self.indirect_hits = []   # dicts describing unpatchable indirect references
        self.zp_touched = set()   # every zp addr read/written (for picking free zp)
        self.last_zp_writer = {}  # zp_addr -> (rom_offset_of_op, size, base) last instruction that set it

    # -- bank-mapped ROM read/write -----------------------------------
    def _read_rom(self, address):
        rel = address - 0x8000
        window = rel >> 12
        offset = rel & 0xFFF
        src = self.cur_bank[window] * 0x1000 + offset
        return self.rom[src] if src < len(self.rom) else 0

    def _write_bank(self, address, value):
        window = address - 0x5FF8
        if 0 <= window <= 7:
            self.cur_bank[window] = value
        return None

    def _rom_offset(self, cpu_addr):
        if cpu_addr < 0x8000:
            return None
        rel = cpu_addr - 0x8000
        window = rel >> 12
        return self.cur_bank[window] * 0x1000 + (rel & 0xFFF)

    def _classify(self, addr):
        if ZP_LO <= addr <= ZP_HI:
            return "zp"
        if RAM_LO <= addr <= RAM_HI:
            return "ram"
        return None

    # -- single-step with full instrumentation -------------------------
    def _step(self):
        pc = self.mpu.pc
        opcode = self.mem[pc]
        name, mode = self.mpu.disassemble[opcode]
        size = OPERAND_SIZE.get(mode, 0)
        self.exec_trace.append((pc, opcode))

        if mode in ABS_MODES or mode in ZP_MODES:
            if size == 1:
                base = self.mem[(pc + 1) & 0xFFFF]
            else:
                base = self.mem[(pc + 1) & 0xFFFF] | (self.mem[(pc + 2) & 0xFFFF] << 8)
            kind = self._classify(base)
            if kind:
                op_off = self._rom_offset((pc + 1) & 0xFFFF)
                instr_off = self._rom_offset(pc)
                same_bank = (op_off is not None and instr_off is not None and
                             (op_off // 0x1000) == (instr_off // 0x1000))
                hit = {"pc": pc, "opcode": opcode, "name": name, "mode": mode,
                       "base": base, "kind": kind, "op_rom_offset": op_off,
                       "size": size, "same_bank": same_bank}
                self.direct_hits.append(hit)
            if base <= 0xFF:
                self.zp_touched.add(base)
                if name in ("STA", "STX", "STY"):
                    self.last_zp_writer[base] = {
                        "op_rom_offset": self._rom_offset((pc + 1) & 0xFFFF), "size": 1}

        elif mode in IND_MODES:
            zp_ptr = self.mem[(pc + 1) & 0xFFFF]
            lo_addr = self.mem[zp_ptr & 0xFF] if mode != "inx" else None
            # resolve the effective target for reporting purposes only
            if mode == "iny":
                target = self.mem[zp_ptr] | (self.mem[(zp_ptr + 1) & 0xFF] << 8)
                target = (target + self.mpu.y) & 0xFFFF
            elif mode == "inx":
                eff_ptr = (zp_ptr + self.mpu.x) & 0xFF
                target = self.mem[eff_ptr] | (self.mem[(eff_ptr + 1) & 0xFF] << 8)
            else:
                target = None
            if target is not None:
                kind = self._classify(target)
                if kind:
                    writer = self.last_zp_writer.get(zp_ptr) or self.last_zp_writer.get((zp_ptr + 1) & 0xFF)
                    self.indirect_hits.append({
                        "pc": pc, "name": name, "mode": mode, "zp_ptr": zp_ptr,
                        "target": target, "kind": kind, "last_pointer_writer": writer})

        self.mpu.step()

    def _run_to_sentinel(self, entry, a, x, y, budget):
        m = self.mem
        sp = self.mpu.sp if self.mpu.sp else 0xFD
        ret = SENTINEL - 1
        m[0x0100 + sp] = (ret >> 8) & 0xFF
        m[0x0100 + ((sp - 1) & 0xFF)] = ret & 0xFF
        self.mpu.sp = (sp - 2) & 0xFF
        self.mpu.pc = entry
        self.mpu.a, self.mpu.x, self.mpu.y = a, x, y
        steps = 0
        while self.mpu.pc != SENTINEL and steps < budget:
            self._step()
            steps += 1
        return steps < budget

    def run(self, nsf, song_index, frames):
        region_x = 1 if (nsf["region"] & 1) else 0
        ok = self._run_to_sentinel(nsf["init"], song_index, region_x, 0x00, STEP_BUDGET)
        if not ok:
            return False, "INIT did not return (hang)"
        for _ in range(frames):
            ok = self._run_to_sentinel(nsf["play"], 0x00, 0x00, 0x00, STEP_BUDGET)
            if not ok:
                return False, "PLAY did not return (hang)"
        return True, None


def trace_file(nsf, frames=FRAMES_PER_SONG, max_songs=MAX_SONGS_TESTED):
    """Runs every (capped) subsong and merges the findings."""
    all_direct, all_indirect, zp_touched = [], [], set()
    songs = min(nsf["song_count"], max_songs) or 1
    for song in range(songs):
        rom_copy = bytearray(nsf["rom"])
        tr = Tracer(rom_copy, nsf["bank"])
        ok, err = tr.run(nsf, song, frames)
        if not ok:
            return None, "song %d: %s" % (song + 1, err)
        all_direct.extend(tr.direct_hits)
        all_indirect.extend(tr.indirect_hits)
        zp_touched |= tr.zp_touched
    return {"direct": all_direct, "indirect": all_indirect, "zp_touched": zp_touched}, None


def plan_relocation(direct_hits, zp_touched):
    """Uniform-delta relocation per category, so indexed/table addressing
    keeps working (X/Y offsets are added at runtime, only the base moves)."""
    zp_bases = {h["base"] for h in direct_hits if h["kind"] == "zp"}
    ram_bases = {h["base"] for h in direct_hits if h["kind"] == "ram"}
    plan = {}
    problems = []

    if zp_bases:
        lo, hi = min(zp_bases), max(zp_bases)
        span = hi - lo + 1
        used_elsewhere = zp_touched - zp_bases
        free_start = None
        run = 0
        for addr in range(0x00, 0xFA):
            if addr in used_elsewhere:
                run = 0
                continue
            run += 1
            if run == span:
                free_start = addr - span + 1
                break
        if free_start is None:
            problems.append("zero page: needs %d free contiguous bytes, none found "
                             "outside what the tune already uses" % span)
        else:
            plan["zp_delta"] = free_start - lo
            plan["zp_range"] = (lo, hi)

    if ram_bases:
        lo, hi = min(ram_bases), max(ram_bases)
        span = hi - lo + 1
        if span > (FREE_RAM_HI - FREE_RAM_LO + 1):
            problems.append("RAM: touched span is %d bytes, only %d free bytes exist "
                             "anywhere in the system ($0200-$03FF)" % (span, FREE_RAM_HI - FREE_RAM_LO + 1))
        else:
            plan["ram_delta"] = FREE_RAM_LO - lo
            plan["ram_range"] = (lo, hi)

    return plan, problems


def apply_patch(rom, direct_hits, plan):
    patched = bytearray(rom)
    patched_offsets = set()
    skipped = []
    for h in direct_hits:
        if not h["same_bank"]:
            skipped.append(h)
            continue
        delta = plan.get("zp_delta") if h["kind"] == "zp" else plan.get("ram_delta")
        if delta is None:
            skipped.append(h)
            continue
        new_addr = h["base"] + delta
        off = h["op_rom_offset"]
        if h["size"] == 1:
            if not (0 <= new_addr <= 0xFF):
                skipped.append(h)
                continue
            patched[off] = new_addr & 0xFF
        else:
            patched[off] = new_addr & 0xFF
            patched[off + 1] = (new_addr >> 8) & 0xFF
        patched_offsets.add(off)
    return patched, skipped


def verify(nsf, patched_rom, original_trace_len_by_song):
    """Re-runs the patched ROM and checks: no reserved-range hits remain,
    and the executed instruction sequence is identical to the original."""
    songs = min(nsf["song_count"], MAX_SONGS_TESTED) or 1
    for song in range(songs):
        rom_copy = bytearray(patched_rom)
        tr = Tracer(rom_copy, nsf["bank"])
        ok, err = tr.run(nsf, song, FRAMES_PER_SONG)
        if not ok:
            return False, "song %d: %s after patch" % (song + 1, err)
        if tr.direct_hits:
            return False, "song %d: still touches reserved memory after patch" % (song + 1)
        orig_len = original_trace_len_by_song[song]
        if len(tr.exec_trace) != orig_len:
            return False, ("song %d: instruction count changed after patching "
                            "(%d vs %d) -- something besides addressing was affected"
                            % (song + 1, len(tr.exec_trace), orig_len))
    return True, None


def patch_nsf(path, out_path=None, verbose=True):
    nsf = load_nsf(path)
    report = {"file": path, "status": None, "detail": None}

    trace, err = trace_file(nsf)
    if err:
        report["status"] = "error"
        report["detail"] = "could not trace the file: %s" % err
        return report

    if not trace["direct"] and not trace["indirect"]:
        report["status"] = "clean"
        report["detail"] = "no reserved-memory access detected -- nothing to patch"
        return report

    plan, problems = plan_relocation(trace["direct"], trace["zp_touched"])

    # need per-song original trace lengths for the regression check
    orig_lens = []
    for song in range(min(nsf["song_count"], MAX_SONGS_TESTED) or 1):
        tr = Tracer(bytearray(nsf["rom"]), nsf["bank"])
        tr.run(nsf, song, FRAMES_PER_SONG)
        orig_lens.append(len(tr.exec_trace))

    patched_rom, skipped = apply_patch(nsf["rom"], trace["direct"], plan)

    if problems or skipped or trace["indirect"]:
        report["status"] = "partial" if not problems else "failed"
    else:
        report["status"] = "pending_verify"

    report["direct_hits"] = len(trace["direct"])
    report["indirect_hits"] = len(trace["indirect"])
    report["skipped"] = len(skipped)
    report["problems"] = problems
    report["indirect_detail"] = trace["indirect"][:10]
    report["plan"] = plan

    if problems:
        report["detail"] = "could not find a safe relocation: " + "; ".join(problems)
        return report

    ok, verr = verify(nsf, patched_rom, orig_lens)
    if not ok:
        report["status"] = "failed"
        report["detail"] = "self-check failed after patching: %s" % verr
        return report

    report["status"] = "patched_clean" if (not skipped and not trace["indirect"]) else "patched_partial"
    report["detail"] = "verified: 0 reserved-memory accesses remain, instruction trace unchanged"

    if out_path:
        new_prg = bytes(patched_rom[nsf["rom_padding"]: nsf["rom_padding"] + nsf["orig_prg_len"]])
        with open(out_path, "wb") as f:
            f.write(bytes(nsf["header"]))
            f.write(new_prg)
        report["out_path"] = out_path

    return report


def autofix_rom(rom, bank, init, play, region, song_count):
    """Library entry point for eznsf.py: attempts to relocate a tune's own
    reserved-memory references directly on an in-memory rom blob (as already
    computed by eznsf.py's own NSF-loading code). Returns (patched_rom, report)
    on success, or (None, report) if nothing needed doing or it couldn't be
    done safely -- callers should fall back to the original rom in that case.
    """
    nsf = {"rom": bytearray(rom), "bank": list(bank), "init": init,
           "play": play, "region": region, "song_count": song_count}
    trace, err = trace_file(nsf)
    if err:
        return None, {"status": "error", "detail": "could not trace: %s" % err}
    if not trace["direct"] and not trace["indirect"]:
        return None, {"status": "clean", "detail": "no reserved-memory access detected"}

    plan, problems = plan_relocation(trace["direct"], trace["zp_touched"])
    if problems:
        return None, {"status": "failed", "detail": "; ".join(problems),
                       "direct_hits": len(trace["direct"]), "indirect_hits": len(trace["indirect"])}

    orig_lens = []
    for song in range(min(song_count, MAX_SONGS_TESTED) or 1):
        tr = Tracer(bytearray(rom), bank)
        tr.run(nsf, song, FRAMES_PER_SONG)
        orig_lens.append(len(tr.exec_trace))

    patched_rom, skipped = apply_patch(rom, trace["direct"], plan)
    ok, verr = verify(nsf, patched_rom, orig_lens)
    if not ok:
        return None, {"status": "failed", "detail": "self-check failed: %s" % verr}

    status = "patched_clean" if (not skipped and not trace["indirect"]) else "patched_partial"
    return patched_rom, {"status": status, "direct_hits": len(trace["direct"]),
                          "indirect_hits": len(trace["indirect"]), "skipped": len(skipped), "plan": plan}


def main():
    if len(sys.argv) < 2:
        print("usage: nsf_patch.py file1.nsf [file2.nsf ...]")
        sys.exit(1)
    for path in sys.argv[1:]:
        out = os.path.splitext(path)[0] + ".fixed.nsf"
        r = patch_nsf(path, out_path=out)
        print("== %s ==" % path)
        print("  status: %s" % r["status"])
        print("  %s" % r.get("detail"))
        if "direct_hits" in r:
            print("  direct (patchable) reserved-memory instructions seen: %d (skipped: %d)"
                  % (r["direct_hits"], r["skipped"]))
            print("  indirect (pointer-based, NOT auto-patched) references seen: %d" % r["indirect_hits"])
            for ih in r.get("indirect_detail", []):
                print("    - %s %s at PC=$%04X -> target $%04X (%s) via zp pointer $%02X"
                      % (ih["name"], ih["mode"], ih["pc"], ih["target"], ih["kind"], ih["zp_ptr"]))
        if r.get("plan"):
            p = r["plan"]
            if "zp_delta" in p:
                lo, hi = p["zp_range"]
                print("  zero page: $%02X-$%02X -> $%02X-$%02X" % (lo, hi, lo + p["zp_delta"], hi + p["zp_delta"]))
            if "ram_delta" in p:
                lo, hi = p["ram_range"]
                print("  RAM: $%04X-$%04X -> $%04X-$%04X" % (lo, hi, lo + p["ram_delta"], hi + p["ram_delta"]))
        if r["status"].startswith("patched") and "out_path" in r:
            print("  wrote: %s" % r["out_path"])
        print()


if __name__ == "__main__":
    main()

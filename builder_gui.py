import os
import sys
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinterdnd2 import DND_FILES, TkinterDnD

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

def parse_nsf_tags(filepath):
    try:
        with open(filepath, 'rb') as f:
            header = f.read(0x80)
            if header[:5] != b'NESM\x1A':
                return None
            title = header[0x0E:0x2E].split(b'\x00')[0].decode('utf-8', 'ignore').strip()
            artist = header[0x2E:0x4E].split(b'\x00')[0].decode('utf-8', 'ignore').strip()
            copyright_val = header[0x4E:0x6E].split(b'\x00')[0].decode('utf-8', 'ignore').strip()
            song_count = header[0x06]
            return {'title': title, 'artist': artist, 'copyright': copyright_val, 'songs': song_count}
    except Exception:
        return None

class EZNSFBuilder(TkinterDnD.Tk):
    def __init__(self):
        super().__init__()
        self.title("EZNSF Rom Builder v1.0")
        self.geometry("680x680")

        # Maps tree item id -> full NSF path, since the tree only displays
        # the basename. Lets NSFs live anywhere, not just the project root.
        self.nsf_full_paths = {}
        
        self.rom_settings_frame = ttk.LabelFrame(self, text="ROM Settings")
        self.rom_settings_frame.pack(fill=tk.X, padx=10, pady=5)
        
        fields = [("TITLE", "My Album"), ("COPYRIGHT", "2026"), 
                  ("PLAY ALL", "// PLAY ALL"), ("INFOTEXT", "// INFO"), 
                  ("LOOPTIME", "05:00")]
        
        self.entries = {}
        for idx, (lbl, default) in enumerate(fields):
            ttk.Label(self.rom_settings_frame, text=lbl).grid(row=idx//3, column=(idx%3)*2, padx=5, pady=2, sticky=tk.W)
            ent = ttk.Entry(self.rom_settings_frame, width=20)
            ent.insert(0, default)
            ent.grid(row=idx//3, column=(idx%3)*2+1, padx=5, pady=2)
            self.entries[lbl] = ent

        self.autofix_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            self.rom_settings_frame,
            text="AUTOFIX NSFs",
            variable=self.autofix_var
        ).grid(row=1, column=4, columnspan=2, padx=5, pady=2, sticky=tk.W)

        self.autonumerate_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self.rom_settings_frame,
            text="AUTONUMERATE",
            variable=self.autonumerate_var
        ).grid(row=2, column=0, columnspan=6, padx=5, pady=2, sticky=tk.W)

        ttk.Label(self.rom_settings_frame, text="INFO Screen Text (multiline):").grid(row=3, column=0, columnspan=2, sticky=tk.W, padx=5)
        self.info_text = tk.Text(self.rom_settings_frame, height=4, width=80)
        self.info_text.insert(tk.END, "This album was\ncreated using EZNSF\nmusic ROM tool,\nby Brad Smith, 2016.\n\nModified to support\nmultiple NSFs\nby AceMan in 2026\nusing Claude AI model\n\nA/START - Play/Toggle Loop\nB - Cancel (Back)\nSTART - Pause")
        self.info_text.grid(row=4, column=0, columnspan=6, padx=5, pady=5)

        self.list_frame = ttk.LabelFrame(self, text="NSF Tracklist (Drag & Drop NSF files here, Double-Click to Edit)")
        self.list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.list_toolbar = ttk.Frame(self.list_frame)
        self.list_toolbar.pack(fill=tk.X, padx=5, pady=2)
        ttk.Button(self.list_toolbar, text="Move Up", command=self.move_up).pack(side=tk.LEFT, padx=2)
        ttk.Button(self.list_toolbar, text="Move Down", command=self.move_down).pack(side=tk.LEFT, padx=2)
        ttk.Button(self.list_toolbar, text="Delete Selected", command=self.delete_selected).pack(side=tk.LEFT, padx=2)
        
        self.tree = ttk.Treeview(
            self.list_frame, 
            columns=("File", "Subsong", "Artist", "Title", "Copyright", "Time"), 
            show="headings", 
            selectmode="extended"
        )
        self.tree.heading("File", text="File")
        self.tree.heading("Subsong", text="Subsong")
        self.tree.heading("Artist", text="Artist")
        self.tree.heading("Title", text="Title")
        self.tree.heading("Copyright", text="Copyright")
        self.tree.heading("Time", text="Time")
        
        self.tree.column("File", width=50, stretch=True)
        self.tree.column("Subsong", width=60, stretch=False, anchor="center")
        self.tree.column("Artist", width=150, stretch=True)
        self.tree.column("Title", width=150, stretch=True)
        self.tree.column("Copyright", width=100, stretch=True)
        self.tree.column("Time", width=80, stretch=False, anchor="center")
        self.tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.tree.bind("<Double-1>", self.on_double_click)
        
        self.tree.drop_target_register(DND_FILES)
        self.tree.dnd_bind('<<Drop>>', self.on_drop)
        
        self.btn_frame = ttk.Frame(self)
        self.btn_frame.pack(fill=tk.X, padx=10, pady=10)
        ttk.Button(self.btn_frame, text="Add NSF Manually", command=self.add_nsf).pack(side=tk.LEFT, padx=5)
        ttk.Button(self.btn_frame, text="Load Album", command=self.load_album).pack(side=tk.LEFT, padx=5)
        ttk.Button(self.btn_frame, text="Clear List", command=self.clear_list).pack(side=tk.LEFT, padx=5)
        
        bold_style = ttk.Style()
        bold_style.configure("Bold.TButton", font=('Sans', 10, 'bold'))
        self.build_button = ttk.Button(self.btn_frame, text="Build ROM", style="Bold.TButton", command=self.build_rom)
        self.build_button.pack(side=tk.RIGHT, padx=5)

    def add_item_to_tree(self, file_name, subsong, artist, title, copyright_val, time_val, full_path=None):
        iid = self.tree.insert("", "end", values=(file_name, subsong, artist, title, copyright_val, time_val))
        # Remember the real source path (may be outside the project folder);
        # the tree cell only ever shows the basename.
        self.nsf_full_paths[iid] = full_path if full_path else file_name
        return iid

    def clear_list(self):
        self.tree.delete(*self.tree.get_children())
        self.nsf_full_paths.clear()

    def move_up(self):
        for item in self.tree.selection():
            idx = self.tree.index(item)
            if idx > 0:
                self.tree.move(item, self.tree.parent(item), idx - 1)

    def move_down(self):
        for item in reversed(self.tree.selection()):
            idx = self.tree.index(item)
            if idx < len(self.tree.get_children()) - 1:
                self.tree.move(item, self.tree.parent(item), idx + 1)
                
    def delete_selected(self):
        for item in self.tree.selection():
            self.nsf_full_paths.pop(item, None)
            self.tree.delete(item)

    def on_double_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell": 
            return
        
        item = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if not item or not column: 
            return
            
        col_index = int(column[1:]) - 1
        x, y, width, height = self.tree.bbox(item, column)
        
        values = self.tree.item(item, 'values')
        current_value = str(values[col_index])
        
        entry = ttk.Entry(self.tree)
        entry.place(x=x, y=y, width=width, height=height)
        entry.insert(0, current_value)
        entry.focus()
        entry.select_range(0, tk.END)
        
        def save_edit(event=None):
            new_value = entry.get()
            new_values = list(values)
            new_values[col_index] = new_value
            self.tree.item(item, values=new_values)
            entry.destroy()
            
        entry.bind('<Return>', save_edit)
        entry.bind('<FocusOut>', save_edit)
        entry.bind('<Escape>', lambda e: entry.destroy())

    def on_drop(self, event):
        files = self.splitlist(event.data)
        for file in files:
            self.process_nsf(file)

    def add_nsf(self):
        files = filedialog.askopenfilenames(filetypes=[("NSF Files", "*.nsf")])
        if files:
            for file in files:
                self.process_nsf(file)
            
    def process_nsf(self, file_path):
        file_path = os.path.abspath(file_path)
        filename = os.path.basename(file_path)
        tags = parse_nsf_tags(file_path)
        
        artist = tags['artist'] if (tags and tags['artist']) else "Unknown Artist"
        copyright_val = tags['copyright'] if (tags and tags['copyright']) else ""
        song_count = tags['songs'] if tags else 1
        base_title = tags['title'] if (tags and tags['title']) else "Track"
        
        for i in range(1, song_count + 1):
            track_title = f"{base_title} - {i}" if song_count > 1 else base_title
            self.add_item_to_tree(filename, str(i), artist, track_title, copyright_val, "LOOP", full_path=file_path)

    def load_album(self):
        path = filedialog.askopenfilename(filetypes=[("Album files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read file: {e}")
            return

        self.tree.delete(*self.tree.get_children())
        self.nsf_full_paths.clear()
        self.info_text.delete("1.0", tk.END)

        album_dir = os.path.dirname(os.path.abspath(path))

        def resolve_nsf_path(nsf_ref):
            # album.txt may have been written from a different working
            # directory, so a relative NSF path needs to be tried against
            # the album file's own folder too, not just the cwd.
            if not nsf_ref:
                return nsf_ref
            if os.path.isfile(nsf_ref):
                return os.path.abspath(nsf_ref)
            candidate = os.path.join(album_dir, nsf_ref)
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)
            return nsf_ref  # leave as-is; build will report it as missing

        current_nsf = ""
        current_artist = "Unknown Artist"
        pending = {}
        track_order = []
        info_lines = []

        def flush_pending():
            for tnum in sorted(pending.keys(), key=lambda k: int(k) if str(k).isdigit() else k):
                t = pending[tnum]
                track_order.append(t)
            pending.clear()

        for raw in lines:
            l = raw
            # Comments must occupy the whole line -- see strip_comment() in
            # eznsf.py for why we can't just truncate at any '#' (paths like
            # "Album #1" would get mangled otherwise).
            if l.lstrip().startswith("#"):
                l = ""
            l = l.rstrip("\n\r")
            if not l.strip():
                continue
            parts = l.split(None, 1)
            if not parts:
                continue
            cmd = parts[0]
            rest = parts[1] if len(parts) > 1 else ""

            if cmd == "TITLE":
                self.entries["TITLE"].delete(0, tk.END)
                self.entries["TITLE"].insert(0, rest)
            elif cmd == "COPYRIGHT":
                self.entries["COPYRIGHT"].delete(0, tk.END)
                self.entries["COPYRIGHT"].insert(0, rest)
            elif cmd == "PLAYALL":
                self.entries["PLAY ALL"].delete(0, tk.END)
                self.entries["PLAY ALL"].insert(0, rest)
            elif cmd == "INFOTEXT":
                self.entries["INFOTEXT"].delete(0, tk.END)
                self.entries["INFOTEXT"].insert(0, rest)
            elif cmd == "LOOPTIME":
                self.entries["LOOPTIME"].delete(0, tk.END)
                self.entries["LOOPTIME"].insert(0, rest)
            elif cmd == "AUTOFIX":
                self.autofix_var.set(rest.strip() != "0")
            elif cmd == "AUTONUMERATE":
                self.autonumerate_var.set(rest.strip() != "0")
            elif cmd == "INFO":
                info_lines.append(rest)
            elif cmd == "NSF":
                flush_pending()
                current_nsf = rest.strip()
                current_artist = "Unknown Artist"
            elif cmd == "ARTIST":
                current_artist = rest
            elif cmd == "TRACK":
                tparts = rest.split(None, 1)
                if len(tparts) >= 1:
                    tnum = tparts[0]
                    title = tparts[1] if len(tparts) > 1 else f"Track {tnum}"
                    if tnum not in pending:
                        pending[tnum] = {"nsf": current_nsf, "artist": current_artist, "title": title, "time": "LOOP", "copy": "", "subsong": tnum}
                    else:
                        pending[tnum]["title"] = title
                        pending[tnum]["nsf"] = current_nsf
                        pending[tnum]["artist"] = current_artist
            elif cmd == "TIME":
                tparts = rest.split(None, 1)
                if len(tparts) >= 2:
                    tnum, time_val = tparts[0], tparts[1]
                    if tnum not in pending:
                        pending[tnum] = {"nsf": current_nsf, "artist": current_artist, "title": f"Track {tnum}", "time": time_val, "copy": "", "subsong": tnum}
                    else:
                        pending[tnum]["time"] = time_val
            elif cmd == "COPY":
                tparts = rest.split(None, 1)
                if len(tparts) >= 1:
                    tnum = tparts[0]
                    copy_val = tparts[1] if len(tparts) > 1 else ""
                    if tnum not in pending:
                        pending[tnum] = {"nsf": current_nsf, "artist": current_artist, "title": f"Track {tnum}", "time": "LOOP", "copy": copy_val, "subsong": tnum}
                    else:
                        pending[tnum]["copy"] = copy_val

        flush_pending()

        if info_lines:
            self.info_text.insert(tk.END, "\n".join(info_lines))

        for t in track_order:
            full_path = resolve_nsf_path(t["nsf"])
            display_name = os.path.basename(full_path) if full_path else t["nsf"]
            self.add_item_to_tree(display_name, t["subsong"], t["artist"], t["title"], t["copy"], t["time"], full_path=full_path)

        messagebox.showinfo("Success", f"Loaded album from:\n{path}")
                
    def build_rom(self):
        album_path = os.path.join(PROJECT_DIR, "album.txt")
        with open(album_path, "w", encoding="utf-8") as f:
            f.write("NROM 0\n")
            f.write(f"TITLE {self.entries['TITLE'].get()}\n")
            f.write(f"COPYRIGHT {self.entries['COPYRIGHT'].get()}\n")
            f.write(f"PLAYALL {self.entries['PLAY ALL'].get()}\n")
            f.write(f"INFOTEXT {self.entries['INFOTEXT'].get()}\n")
            f.write(f"LOOPTIME {self.entries['LOOPTIME'].get()}\n\n")
            if self.autofix_var.get():
                f.write("AUTOFIX 1\n\n")
            if self.autonumerate_var.get():
                f.write("AUTONUMERATE 1\n\n")
            
            for line in self.info_text.get("1.0", tk.END).splitlines():
                if line.strip(): 
                    f.write(f"INFO {line}\n")
            f.write("\n")
            
            current_nsf = None
            current_artist = None

            for child in self.tree.get_children():
                row = self.tree.item(child)["values"]
                # Tkinter's Treeview silently converts numeric-looking cell
                # text (e.g. a copyright of just "2026") back to int/float
                # when read via item()["values"], so every field needs an
                # explicit str() here regardless of what it looks like.
                file_name, subsong, artist, title, copyright_val, time_val = (str(v) for v in row)
                nsf_path = self.nsf_full_paths.get(child, file_name)

                if nsf_path != current_nsf:
                    f.write(f"\nNSF {nsf_path}\n")
                    f.write(f"ARTIST {artist}\n")
                    current_nsf = nsf_path
                    current_artist = artist
                elif artist != current_artist:
                    f.write(f"ARTIST {artist}\n")
                    current_artist = artist
                    
                f.write(f"TRACK {subsong} {title}\n")
                f.write(f"TIME {subsong} {time_val}\n")
                if copyright_val.strip():
                    f.write(f"COPY {subsong} {copyright_val}\n")
                    
            f.write("\nSCREEN INFO   screen.nam   tiles.chr tiles.chr colors.pal colors.pal\n")
            f.write("SCREEN TRACKS screen.nam tiles.chr tiles.chr colors.pal colors.pal\n")
            f.write("SCREEN PLAY   screen.nam   tiles.chr tiles.chr colors.pal colors.pal\n")
            
            f.write("\nCOORD INFO              2 2\n")
            f.write("COORD TRACKS_TITLE      2 2\n")
            f.write("COORD TRACKS_COPYRIGHT  2 4\n")
            f.write("COORD TRACKS_TRACK      4 6\n")
            f.write("COORD PLAY_TRACK        2 20\n")
            f.write("COORD PLAY_TIME         16 195\n")
            
            f.write("\nCONST SPRITE_CHOOSE 4\nCONST SPRITE_PLAY 8\nCONST SPRITE_PLAY_ALL 4\n")
            f.write("CONST SPRITE_PAUSE 6\nCONST SPRITE_STOP 7\nCONST SPRITE_ZERO 48\nCONST SPRITE_COLON 58\n")

        self.build_button.config(state=tk.DISABLED, text="Building...")
        threading.Thread(target=self._run_build, args=(album_path,), daemon=True).start()

    def _run_build(self, album_path):
        # Runs python eznsf.py directly (not via eznsf.bat -- that script
        # ends with "pause", which would just hang forever waiting for a
        # keypress that never arrives on a background process), with an
        # explicit working directory so it finds eznsf.s/eznsf.cfg/tools/
        # regardless of where the GUI itself was launched from.
        eznsf_script = os.path.join(PROJECT_DIR, "eznsf.py")
        outdir = os.path.join(PROJECT_DIR, "temp")
        try:
            proc = subprocess.run(
                [sys.executable, eznsf_script, album_path, outdir],
                cwd=PROJECT_DIR, capture_output=True, text=True, timeout=900)
            self.after(0, self._build_finished, proc.returncode, proc.stdout, proc.stderr)
        except Exception as e:
            self.after(0, self._build_finished, -1, "", str(e))

    @staticmethod
    def _parse_nsf_status(output):
        lines = output.splitlines()
        try:
            start = lines.index("==NSF_STATUS==")
            end = lines.index("==END_NSF_STATUS==", start)
        except ValueError:
            return None
        entries = []
        for line in lines[start + 1:end]:
            parts = line.split("|", 2)
            if len(parts) == 3:
                entries.append(parts)
        return entries

    def _build_finished(self, returncode, stdout, stderr):
        self.build_button.config(state=tk.NORMAL, text="Build ROM")
        output = "\n".join(x for x in (stdout, stderr) if x)

        if returncode == 0:
            entries = self._parse_nsf_status(output)
            if entries:
                label = {"patched": "patched", "skipped": "skipped", "unchanged": "unchanged"}
                lines = []
                for fname, status, detail in entries:
                    text = f"{os.path.basename(fname)} - {label.get(status, status)}"
                    if status in ("patched", "skipped"):
                        text += f" ({detail})"
                    lines.append(text)
                msg = "ROM built successfully.\n\n" + "\n".join(lines)
            else:
                tail = "\n".join(output.strip().splitlines()[-30:]) if output.strip() else "(no output)"
                msg = "ROM built successfully!\n\n" + tail
            messagebox.showinfo("Success", msg)
        else:
            tail = "\n".join(output.strip().splitlines()[-30:]) if output.strip() else "(no output)"
            messagebox.showerror("Build failed", f"eznsf.py exited with code {returncode}:\n\n" + tail)

if __name__ == "__main__":
    app = EZNSFBuilder()
    app.mainloop()

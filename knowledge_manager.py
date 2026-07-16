"""Standalone tool for managing the AI ช่วยค้นข้อมูล knowledge base - kept
separate from label_gui.py on purpose (per user request) so non-technical
staff can be handed just this one simple app for updating reference
documents, without touching the main label-printing program at all.

Shows the knowledge/ folder as a tree (folders + files, any depth), lets
staff browse content, create/delete category folders, type/paste new text
entries directly, and import existing PDF/Word files into any folder.
Reuses knowledge.py's own KNOWLEDGE_DIR and text-extraction functions so
this tool can never point at a different folder than what label_gui.py
actually searches, and never duplicates the PDF/docx parsing logic.
"""
import os
import shutil
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import knowledge

APP_TITLE = "จัดการฐานความรู้ AI (Knowledge Base)"


def human_size(num_bytes):
    for unit in ("B", "KB", "MB"):
        if num_bytes < 1024:
            return f"{num_bytes:.0f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} GB"


class KnowledgeManagerApp:
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("1100x760")

        # All fonts here are the original sizes x1.5 (per user request - the
        # default sizing read too small) - keep this factor in mind if
        # tweaking any single widget's font later, so they stay consistent.
        f_btn = ("Tahoma", 15)
        f_path = ("Tahoma", 12)
        f_tree = ("Tahoma", 15)
        f_file_label = ("Tahoma", 15, "bold")
        f_content = ("Tahoma", 17)
        f_hint = ("Tahoma", 12)

        style = ttk.Style()
        style.configure("Treeview", font=f_tree, rowheight=32)
        style.configure("Treeview.Heading", font=f_tree)

        # path -> tree item id, and the reverse, so a tree selection can be
        # mapped back to a real filesystem path and vice versa.
        self._path_by_item = {}

        toolbar = tk.Frame(root)
        toolbar.pack(fill="x", padx=8, pady=6)

        tk.Button(toolbar, text="📁 โฟลเดอร์ใหม่", font=f_btn, command=self.on_new_folder).pack(side="left", padx=2)
        tk.Button(toolbar, text="📝 ไฟล์ข้อความใหม่", font=f_btn, command=self.on_new_text_file).pack(side="left", padx=2)
        tk.Button(toolbar, text="📥 นำเข้าไฟล์ PDF/Word", font=f_btn, command=self.on_import_files).pack(side="left", padx=2)
        tk.Button(toolbar, text="🗑 ลบ", font=f_btn, fg="#a02020", command=self.on_delete).pack(side="left", padx=2)
        tk.Button(toolbar, text="🔄 รีเฟรช", font=f_btn, command=self.refresh_tree).pack(side="left", padx=2)

        tk.Label(
            root,
            text=f"โฟลเดอร์: {knowledge.KNOWLEDGE_DIR}",
            font=f_path, fg="#666", anchor="w",
        ).pack(fill="x", padx=8)

        body = tk.PanedWindow(root, orient="horizontal", sashwidth=6)
        body.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        tree_frame = tk.Frame(body)
        self.tree = ttk.Treeview(tree_frame, show="tree")
        tree_scroll = tk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        body.add(tree_frame, width=440)

        right = tk.Frame(body)
        self.file_label_var = tk.StringVar(value="เลือกไฟล์หรือโฟลเดอร์ทางซ้าย")
        tk.Label(right, textvariable=self.file_label_var, font=f_file_label, anchor="w").pack(
            fill="x", padx=4, pady=(0, 4)
        )
        self.editable = False
        self.current_path = None
        self.content_text = tk.Text(right, wrap="word", font=f_content, state="disabled")
        self.content_text.pack(fill="both", expand=True, padx=4)

        save_row = tk.Frame(right)
        save_row.pack(fill="x", padx=4, pady=6)
        self.save_btn = tk.Button(
            save_row, text="💾 บันทึกการแก้ไข", font=f_btn, command=self.on_save_edit, state="disabled"
        )
        self.save_btn.pack(side="left")
        self.hint_var = tk.StringVar(value="")
        tk.Label(save_row, textvariable=self.hint_var, font=f_hint, fg="#666").pack(side="left", padx=8)
        body.add(right)

        self.refresh_tree()

    # ---------------------------------------------------------------- tree

    def refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        self._path_by_item.clear()
        knowledge.ensure_knowledge_dir()
        root_id = self.tree.insert("", "end", text="📂 knowledge", open=True)
        self._path_by_item[root_id] = knowledge.KNOWLEDGE_DIR
        self._populate(root_id, knowledge.KNOWLEDGE_DIR)

    def _populate(self, parent_item, dir_path):
        try:
            entries = sorted(os.listdir(dir_path))
        except OSError:
            return
        folders = [e for e in entries if os.path.isdir(os.path.join(dir_path, e))]
        files = [e for e in entries if os.path.isfile(os.path.join(dir_path, e)) and e != "README.txt"]
        for name in folders:
            full = os.path.join(dir_path, name)
            item = self.tree.insert(parent_item, "end", text=f"📁 {name}", open=False)
            self._path_by_item[item] = full
            self._populate(item, full)
        for name in files:
            full = os.path.join(dir_path, name)
            icon = {".pdf": "📕", ".docx": "📘"}.get(os.path.splitext(name)[1].lower(), "📄")
            item = self.tree.insert(parent_item, "end", text=f"{icon} {name}")
            self._path_by_item[item] = full

    def _selected_path(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return self._path_by_item.get(sel[0])

    def _selected_folder(self):
        """Folder to act on for 'new folder'/'new file'/'import' - the
        selected folder itself, or the parent folder of a selected file."""
        path = self._selected_path()
        if path is None:
            return knowledge.KNOWLEDGE_DIR
        return path if os.path.isdir(path) else os.path.dirname(path)

    # ---------------------------------------------------------------- view

    def on_select(self, event=None):
        path = self._selected_path()
        if path is None or os.path.isdir(path):
            self.current_path = None
            self.editable = False
            self.file_label_var.set("(โฟลเดอร์) - เลือกไฟล์เพื่อดูเนื้อหา")
            self._set_content("", editable=False)
            self.save_btn.config(state="disabled")
            self.hint_var.set("")
            return

        self.current_path = path
        name = os.path.basename(path)
        ext = os.path.splitext(path)[1].lower()
        rel = os.path.relpath(path, knowledge.KNOWLEDGE_DIR).replace(os.sep, "/")
        size = human_size(os.path.getsize(path))
        self.file_label_var.set(f"{name}  ({rel}, {size})")

        try:
            if ext in (".txt", ".md"):
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
                self._set_content(text, editable=True)
                self.save_btn.config(state="normal")
                self.hint_var.set("แก้ไขได้โดยตรง แล้วกดบันทึก")
            elif ext == ".pdf":
                text = knowledge._extract_pdf_text(path)
                self._set_content(text, editable=False)
                self.save_btn.config(state="disabled")
                self.hint_var.set("ไฟล์ PDF - ดูได้อย่างเดียว แก้ไขไม่ได้ในนี้ (ต้องแก้ต้นฉบับแล้วนำเข้าใหม่)")
            elif ext == ".docx":
                text = knowledge._extract_docx_text(path)
                self._set_content(text, editable=False)
                self.save_btn.config(state="disabled")
                self.hint_var.set("ไฟล์ Word - ดูได้อย่างเดียว แก้ไขไม่ได้ในนี้ (ต้องแก้ต้นฉบับแล้วนำเข้าใหม่)")
            else:
                self._set_content("(นามสกุลไฟล์นี้ระบบไม่รองรับ ไม่ถูกใช้ค้นหา)", editable=False)
                self.save_btn.config(state="disabled")
                self.hint_var.set("")
        except Exception as e:
            self._set_content(f"เปิดไฟล์ไม่สำเร็จ: {e}", editable=False)
            self.save_btn.config(state="disabled")

    def _set_content(self, text, editable):
        self.content_text.config(state="normal")
        self.content_text.delete("1.0", tk.END)
        self.content_text.insert("1.0", text)
        self.content_text.config(state="normal" if editable else "disabled")
        self.editable = editable

    def on_save_edit(self):
        if not self.current_path or not self.editable:
            return
        content = self.content_text.get("1.0", "end-1c")
        try:
            with open(self.current_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            messagebox.showerror("บันทึกไม่สำเร็จ", str(e), parent=self.root)
            return
        messagebox.showinfo("สำเร็จ", "บันทึกแล้ว", parent=self.root)

    # ---------------------------------------------------------------- actions

    def on_new_folder(self):
        target_dir = self._selected_folder()
        name = simpledialog.askstring("โฟลเดอร์ใหม่", "ชื่อโฟลเดอร์หมวดหมู่ใหม่:", parent=self.root)
        if not name:
            return
        name = name.strip()
        if not name or any(c in name for c in '\\/:*?"<>|'):
            messagebox.showerror("ชื่อไม่ถูกต้อง", "ชื่อโฟลเดอร์ห้ามมีอักขระ \\ / : * ? \" < > |", parent=self.root)
            return
        new_path = os.path.join(target_dir, name)
        try:
            os.makedirs(new_path, exist_ok=False)
        except FileExistsError:
            messagebox.showerror("ผิดพลาด", "มีโฟลเดอร์ชื่อนี้อยู่แล้ว", parent=self.root)
            return
        except Exception as e:
            messagebox.showerror("ผิดพลาด", str(e), parent=self.root)
            return
        self.refresh_tree()

    def on_new_text_file(self):
        target_dir = self._selected_folder()
        name = simpledialog.askstring(
            "ไฟล์ข้อความใหม่", "ชื่อไฟล์ (ไม่ต้องใส่ .txt):", parent=self.root
        )
        if not name:
            return
        name = name.strip()
        if not name or any(c in name for c in '\\/:*?"<>|'):
            messagebox.showerror("ชื่อไม่ถูกต้อง", "ชื่อไฟล์ห้ามมีอักขระ \\ / : * ? \" < > |", parent=self.root)
            return
        if not name.lower().endswith((".txt", ".md")):
            name += ".txt"
        new_path = os.path.join(target_dir, name)
        if os.path.exists(new_path):
            messagebox.showerror("ผิดพลาด", "มีไฟล์ชื่อนี้อยู่แล้ว", parent=self.root)
            return
        try:
            with open(new_path, "w", encoding="utf-8") as f:
                f.write("")
        except Exception as e:
            messagebox.showerror("ผิดพลาด", str(e), parent=self.root)
            return
        self.refresh_tree()
        self._select_path(new_path)
        self.content_text.focus_set()

    def on_import_files(self):
        target_dir = self._selected_folder()
        paths = filedialog.askopenfilenames(
            title="เลือกไฟล์ PDF/Word ที่จะนำเข้า",
            filetypes=[("PDF/Word", "*.pdf *.docx"), ("PDF", "*.pdf"), ("Word", "*.docx"), ("ทุกไฟล์", "*.*")],
            parent=self.root,
        )
        if not paths:
            return
        imported, skipped = 0, []
        for src in paths:
            name = os.path.basename(src)
            dest = os.path.join(target_dir, name)
            if os.path.exists(dest):
                if not messagebox.askyesno(
                    "มีไฟล์นี้อยู่แล้ว", f"'{name}' มีอยู่แล้วในโฟลเดอร์นี้ ต้องการเขียนทับไหม?", parent=self.root
                ):
                    skipped.append(name)
                    continue
            try:
                shutil.copy2(src, dest)
                imported += 1
            except Exception as e:
                messagebox.showerror("นำเข้าไม่สำเร็จ", f"{name}: {e}", parent=self.root)
        self.refresh_tree()
        msg = f"นำเข้าสำเร็จ {imported} ไฟล์"
        if skipped:
            msg += f"\nข้าม {len(skipped)} ไฟล์: {', '.join(skipped)}"
        messagebox.showinfo("เสร็จสิ้น", msg, parent=self.root)

    def on_delete(self):
        path = self._selected_path()
        if path is None or path == knowledge.KNOWLEDGE_DIR:
            messagebox.showwarning("แจ้งเตือน", "กรุณาเลือกไฟล์หรือโฟลเดอร์ก่อน", parent=self.root)
            return
        name = os.path.basename(path)
        if os.path.isdir(path):
            has_content = bool(os.listdir(path))
            warn = f"ลบโฟลเดอร์ '{name}'" + (" และไฟล์ทั้งหมดข้างในถาวรใช่ไหม?" if has_content else "ใช่ไหม?")
            if not messagebox.askyesno("ยืนยันการลบ", warn, icon="warning", parent=self.root):
                return
            try:
                shutil.rmtree(path)
            except Exception as e:
                messagebox.showerror("ลบไม่สำเร็จ", str(e), parent=self.root)
                return
        else:
            if not messagebox.askyesno("ยืนยันการลบ", f"ลบไฟล์ '{name}' ถาวรใช่ไหม?", icon="warning", parent=self.root):
                return
            try:
                os.remove(path)
            except Exception as e:
                messagebox.showerror("ลบไม่สำเร็จ", str(e), parent=self.root)
                return
        self.refresh_tree()
        self.on_select()

    def _select_path(self, path):
        for item, p in self._path_by_item.items():
            if p == path:
                self.tree.selection_set(item)
                self.tree.see(item)
                self.on_select()
                return


def main():
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8")
    root = tk.Tk()
    KnowledgeManagerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

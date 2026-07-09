"""GUI 气泡弹窗插件 — 协议 v3.0 系统插件
显示气泡菜单和文本编辑器。
"""
import asyncio
import queue
import threading
import traceback
from typing import Optional

_system: Optional["BubbleSystem"] = None


class BubbleSystem:
    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self._bubble = None
        self._bubble_frame = None
        self._bubble_btns = []
        self._bubble_callback = None
        self._bubble_visible = False
        self._bubble_geom = (0, 0, 0, 0)
        self._bubble_processing = False
        self._editor = None
        self._editor_geom = (0, 0, 0, 0)
        self._tk_queue = queue.Queue()
        self._tk_ready = threading.Event()
    
    def init_tk(self):
        import tkinter as tk
        try:
            self._bubble = tk.Tk()
            self._bubble.overrideredirect(True)
            self._bubble.attributes('-topmost', True)
            self._bubble.configure(bg='#0f172a')
            self._bubble.withdraw()
            self._bubble_frame = tk.Frame(self._bubble, bg='#0f172a', padx=8, pady=8)
            self._bubble_frame.pack()
            self._bubble.bind('<Escape>', lambda e: self._hide())
            self._tk_ready.set()
            self._process_queue()
            self._bubble.mainloop()
        except Exception as e:
            traceback.print_exc()
            self._tk_ready.set()
    
    def _process_queue(self):
        try:
            while True:
                msg = self._tk_queue.get_nowait()
                cmd = msg[0]
                if cmd == "show":
                    self._show(msg[1], msg[2], msg[3], msg[4])
                elif cmd == "hide":
                    self._hide()
                elif cmd == "editor":
                    self._show_editor(msg[1], msg[2], msg[3], msg[4], msg[5])
        except queue.Empty:
            pass
        if self._bubble:
            self._bubble.after(50, self._process_queue)
    
    def show_bubble(self, x, y, options, callback):
        self._tk_queue.put(("show", x, y, options, callback))
    
    def hide_bubble(self):
        self._tk_queue.put(("hide",))
    
    def _show(self, x, y, options, callback):
        import tkinter as tk
        for btn in self._bubble_btns:
            btn.destroy()
        self._bubble_btns.clear()
        self._bubble_callback = callback
        self._bubble_processing = False
        
        for label in (options or []):
            is_tool = label.startswith("🔧") or label.startswith("  ")
            
            btn = tk.Label(
                self._bubble_frame, text=label,
                bg='#6366f1' if not is_tool else '#475569',
                fg='#ffffff' if not is_tool else '#e2e8f0',
                font=('微软雅黑', 14, 'bold'),
                padx=18, pady=10,
                cursor='hand2'
            )
            
            def on_enter(e, b=btn, t=is_tool):
                b.configure(bg='#818cf8' if not t else '#64748b')
            
            def on_leave(e, b=btn, t=is_tool):
                b.configure(bg='#6366f1' if not t else '#475569')
            
            def on_click(e, lbl=label):
                self._bubble_processing = True
                for b in self._bubble_btns:
                    try:
                        b.configure(bg='#a5b4fc', fg='#1e293b')
                    except Exception:
                        pass
                
                def _execute():
                    for b in self._bubble_btns:
                        try:
                            b.configure(bg='#334155', fg='#94a3b8')
                        except Exception:
                            pass
                    cb = self._bubble_callback
                    if cb:
                        cb(lbl)
                    self._bubble_processing = False
                
                self._bubble.after(100, _execute)
            
            btn.bind('<Enter>', on_enter)
            btn.bind('<Leave>', on_leave)
            btn.bind('<Button-1>', on_click)
            
            btn.pack(side='left', padx=4, pady=4)
            self._bubble_btns.append(btn)
        
        screen_w = self._bubble.winfo_screenwidth()
        self._bubble.update_idletasks()
        w = self._bubble.winfo_reqwidth()
        bx = x + 10
        by = y + 50
        if bx + w > screen_w:
            bx = screen_w - w - 10
        self._bubble.geometry(f"+{bx}+{by}")
        self._bubble.update_idletasks()
        self._bubble_geom = (
            self._bubble.winfo_x(), self._bubble.winfo_y(),
            self._bubble.winfo_width(), self._bubble.winfo_height()
        )
        self._bubble.deiconify()
        self._bubble_visible = True
        self._bubble.lift()
    
    def _hide(self):
        if self._bubble:
            self._bubble.withdraw()
        self._bubble_visible = False
        self._bubble_processing = False
    
    def show_editor(self, x, y, title, text, on_close=None):
        self._tk_queue.put(("editor", x, y, title, text, on_close))
    
    def _show_editor(self, x, y, title, text, on_close):
        import tkinter as tk
        self._hide()
        
        if self._editor:
            try:
                self._editor.destroy()
            except Exception:
                pass
        
        editor = tk.Toplevel(self._bubble)
        editor.overrideredirect(True)
        editor.attributes('-topmost', True)
        editor.configure(bg='#1e293b')
        
        title_bar = tk.Frame(editor, bg='#0f172a', padx=16, pady=12)
        title_bar.pack(fill='x')
        tk.Label(title_bar, text=title, bg='#0f172a', fg='#e2e8f0',
                 font=('微软雅黑', 10, 'bold')).pack(side='left')
        
        text_frame = tk.Frame(editor, bg='#1e293b', padx=10, pady=6)
        text_frame.pack(fill='both', expand=True)
        
        text_widget = tk.Text(
            text_frame, bg='#1e293b', fg='#f1f5f9',
            font=('微软雅黑', 10), wrap='word',
            padx=14, pady=12, bd=0,
            width=60, height=20,
            insertbackground='#818cf8'
        )
        text_widget.pack(fill='both', expand=True)
        text_widget.insert('1.0', text)
        text_widget.focus_set()
        
        btn_bar = tk.Frame(editor, bg='#0f172a', padx=12, pady=12)
        btn_bar.pack(fill='x')
        
        def copy_all():
            editor.clipboard_clear()
            editor.clipboard_append(text_widget.get('1.0', 'end-1c'))
            copy_btn.configure(text="✅ 已复制", fg='#22c55e')
            editor.after(1500, lambda: copy_btn.configure(text="📋 复制全部", fg='#e2e8f0'))
        
        copy_btn = tk.Label(
            btn_bar, text="📋 复制全部",
            bg='#334155', fg='#e2e8f0',
            font=('微软雅黑', 13), padx=18, pady=10, cursor='hand2'
        )
        copy_btn.pack(side='left', padx=4)
        copy_btn.bind('<Button-1>', lambda e: copy_all())
        copy_btn.bind('<Enter>', lambda e: copy_btn.configure(bg='#475569'))
        copy_btn.bind('<Leave>', lambda e: copy_btn.configure(bg='#334155'))
        
        def close():
            editor.destroy()
            self._editor = None
            if on_close:
                on_close()
        
        close_btn = tk.Label(
            btn_bar, text="✅ 关闭",
            bg='#6366f1', fg='#ffffff',
            font=('微软雅黑', 13, 'bold'), padx=18, pady=10, cursor='hand2'
        )
        close_btn.pack(side='right', padx=4)
        close_btn.bind('<Button-1>', lambda e: close())
        close_btn.bind('<Enter>', lambda e: close_btn.configure(bg='#818cf8'))
        close_btn.bind('<Leave>', lambda e: close_btn.configure(bg='#6366f1'))
        
        editor.update_idletasks()
        screen_w = editor.winfo_screenwidth()
        screen_h = editor.winfo_screenheight()
        ew = editor.winfo_reqwidth()
        eh = editor.winfo_reqheight()
        ex = screen_w - ew - 80
        ey = (screen_h - eh) // 2
        editor.geometry(f"+{ex}+{ey}")
        editor.update_idletasks()
        self._editor_geom = (editor.winfo_x(), editor.winfo_y(),
                             editor.winfo_width(), editor.winfo_height())
        self._editor = editor
        editor.lift()
        editor.bind('<Escape>', lambda e: close())


import core


async def execute(envelop, agent):
    global _system
    
    if _system is None:
        _system = BubbleSystem(agent.loop)
        threading.Thread(target=_system.init_tk, daemon=True, name="bubble-tk").start()
        _system._tk_ready.wait(timeout=10)
    
    action = envelop.payload.get("action", "")
    
    if action == "show":
        x = envelop.payload.get("x", 0)
        y = envelop.payload.get("y", 0)
        options = envelop.payload.get("options", [])
        
        result_holder = {}
        event = threading.Event()
        
        def callback(selected):
            result_holder["selected"] = selected
            event.set()
        
        _system.show_bubble(x, y, options, callback)
        
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, event.wait)
        
        envelop.payload = {"selected": result_holder.get("selected")}
        return envelop
    
    elif action == "editor":
        x = envelop.payload.get("x", 0)
        y = envelop.payload.get("y", 0)
        title = envelop.payload.get("title", "Editor")
        text = envelop.payload.get("text", "")
        
        _system.show_editor(x, y, title, text)
        envelop.payload = {"ok": True}
        return envelop
    
    elif action == "hide":
        _system.hide_bubble()
        envelop.payload = {"ok": True}
        return envelop
    
    envelop.payload = {"error": f"Unknown action: {action}"}
    return envelop
#!/usr/bin/env python3
"""
Adopt Me Bot GUI Overlay
Provides a graphical interface to control the bot and view debug output.
"""

import sys
import os
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import time
from io import StringIO

# Add parent directory to path to import adoptme_enhanced
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import adoptme_enhanced as bot
except ImportError as e:
    print(f"ERROR: Could not import adoptme_enhanced: {e}")
    print("Make sure adoptme_enhanced.py is in the same directory as adoptme_gui.py")
    sys.exit(1)


class DebugConsole:
    """Captures stdout and displays it in a text widget."""
    def __init__(self, text_widget):
        self.text_widget = text_widget
        self.original_stdout = sys.stdout
        
    def write(self, message):
        self.text_widget.config(state=tk.NORMAL)
        self.text_widget.insert(tk.END, message)
        self.text_widget.see(tk.END)
        self.text_widget.config(state=tk.DISABLED)
        self.text_widget.update()
        
    def flush(self):
        pass
    
    def restore(self):
        sys.stdout = self.original_stdout


class AdoptMeGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Adopt Me Bot Control")
        
        # Position on right side of screen and make it stay on top
        self.root.geometry("320x600+1600+50")
        self.root.resizable(False, False)
        self.root.attributes('-topmost', True)  # Always on top
        self.root.attributes('-alpha', 0.95)  # Slightly transparent
        
        # Configure style
        style = ttk.Style()
        style.theme_use('clam')
        
        # Color scheme
        self.bg_color = "#1e1e1e"
        self.fg_color = "#ffffff"
        self.accent_color = "#0d7377"
        self.warning_color = "#d62828"
        self.success_color = "#06a77d"
        
        self.root.configure(bg=self.bg_color)
        
        # Create main layout
        self.create_widgets()
        
        # Setup debug console capture
        self.debug_console = DebugConsole(self.debug_text)
        sys.stdout = self.debug_console
        
        # Print startup message
        print("\n" + "="*70)
        print("ADOPT ME BOT - CONTROL PANEL")
        print("="*70)
        print("[INFO] GUI initialized")
        print(f"[INFO] Config: {bot.CONFIG_FILE}")
        print(f"[INFO] Needs:  {bot.NEEDS_DIR}")
        print("="*70 + "\n")
        
        # Thread management
        self.current_thread = None
        self.is_running = False
        
    def create_widgets(self):
        """Create all GUI widgets."""
        
        # ===== TOP FRAME: TITLE =====
        title_frame = tk.Frame(self.root, bg=self.accent_color, height=40)
        title_frame.pack(fill=tk.X, padx=0, pady=0)
        title_frame.pack_propagate(False)
        
        title_label = tk.Label(
            title_frame,
            text="🤖 ADOPT ME",
            font=("Courier", 12, "bold"),
            bg=self.accent_color,
            fg=self.fg_color
        )
        title_label.pack(pady=8)
        
        # ===== MAIN BUTTONS FRAME =====
        button_frame = tk.Frame(self.root, bg=self.bg_color)
        button_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        
        # Main workflow button (large)
        self.btn_workflow = tk.Button(
            button_frame,
            text="▶ START",
            command=self.run_workflow,
            font=("Courier", 11, "bold"),
            bg="#2ecc71",
            fg=self.fg_color,
            activebackground="#27ae60",
            relief=tk.RAISED,
            height=3,
            cursor="hand2"
        )
        self.btn_workflow.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Stop button
        self.btn_stop = tk.Button(
            button_frame,
            text="⏹ STOP",
            command=self.stop_action,
            font=("Courier", 11, "bold"),
            bg=self.warning_color,
            fg=self.fg_color,
            activebackground="#b91c1c",
            relief=tk.RAISED,
            height=2,
            cursor="hand2"
        )
        self.btn_stop.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Separator
        separator = tk.Frame(button_frame, bg=self.accent_color, height=1)
        separator.pack(fill=tk.X, pady=5)
        
        # Individual functions label
        functions_label = tk.Label(
            button_frame,
            text="Functions",
            font=("Courier", 9, "bold"),
            bg=self.bg_color,
            fg=self.accent_color
        )
        functions_label.pack(anchor=tk.W, pady=(5, 3))
        
        # Small buttons for individual functions
        self.btn_respawn = tk.Button(
            button_frame,
            text="1️⃣ Respawn",
            command=self.run_respawn,
            font=("Courier", 9),
            bg=self.accent_color,
            fg=self.fg_color,
            activebackground="#105c7a",
            relief=tk.FLAT,
            height=1,
            cursor="hand2"
        )
        self.btn_respawn.pack(fill=tk.X, pady=2)
        
        self.btn_buttons = tk.Button(
            button_frame,
            text="2️⃣ Detect Buttons",
            command=self.run_detect_buttons,
            font=("Courier", 9),
            bg=self.accent_color,
            fg=self.fg_color,
            activebackground="#105c7a",
            relief=tk.FLAT,
            height=1,
            cursor="hand2"
        )
        self.btn_buttons.pack(fill=tk.X, pady=2)
        
        self.btn_needs = tk.Button(
            button_frame,
            text="3️⃣ Process Needs",
            command=self.run_process_needs,
            font=("Courier", 9),
            bg=self.accent_color,
            fg=self.fg_color,
            activebackground="#105c7a",
            relief=tk.FLAT,
            height=1,
            cursor="hand2"
        )
        self.btn_needs.pack(fill=tk.X, pady=2)
        
        # ===== DEBUG SECTION =====
        debug_frame = tk.Frame(self.root, bg=self.bg_color)
        debug_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        
        debug_label = tk.Label(
            debug_frame,
            text="Debug",
            font=("Courier", 9, "bold"),
            bg=self.bg_color,
            fg=self.accent_color
        )
        debug_label.pack(anchor=tk.W, pady=(0, 3))
        
        # Compact debug text area
        self.debug_text = scrolledtext.ScrolledText(
            debug_frame,
            height=12,
            width=38,
            bg="#0a0a0a",
            fg="#00ff00",
            font=("Courier", 7),
            insertbackground="#00ff00",
            state=tk.DISABLED
        )
        self.debug_text.pack(fill=tk.BOTH, expand=True)
        
        # ===== BOTTOM STATUS BAR =====
        status_frame = tk.Frame(self.root, bg=self.accent_color, height=25)
        status_frame.pack(fill=tk.X, side=tk.BOTTOM)
        status_frame.pack_propagate(False)
        
        self.status_label = tk.Label(
            status_frame,
            text="Ready",
            font=("Courier", 8),
            bg=self.accent_color,
            fg=self.fg_color
        )
        self.status_label.pack(anchor=tk.W, padx=8, pady=3)
    
    def update_status(self, message):
        """Update status bar."""
        self.status_label.config(text=message)
        self.root.update()
    
    def run_async(self, func, *args):
        """Run a function in a separate thread."""
        if self.is_running:
            messagebox.showwarning("Already Running", "Process running. Click STOP.")
            return
        
        self.is_running = True
        self.update_status("Running...")
        self.disable_buttons()
        
        def thread_func():
            try:
                func(*args)
                self.update_status("✓ Complete")
            except Exception as e:
                print(f"\n[ERROR] {str(e)}")
                self.update_status(f"✗ Error")
            finally:
                self.is_running = False
                self.enable_buttons()
        
        self.current_thread = threading.Thread(target=thread_func, daemon=True)
        self.current_thread.start()
    
    def disable_buttons(self):
        """Disable all control buttons."""
        self.btn_workflow.config(state=tk.DISABLED)
        self.btn_respawn.config(state=tk.DISABLED)
        self.btn_buttons.config(state=tk.DISABLED)
        self.btn_needs.config(state=tk.DISABLED)
    
    def enable_buttons(self):
        """Enable all control buttons."""
        self.btn_workflow.config(state=tk.NORMAL)
        self.btn_respawn.config(state=tk.NORMAL)
        self.btn_buttons.config(state=tk.NORMAL)
        self.btn_needs.config(state=tk.NORMAL)
    
    # ===== ACTION CALLBACKS =====
    
    def run_workflow(self):
        """Run full workflow."""
        print("\n[▶] START WORKFLOW\n")
        self.run_async(bot.run_full_workflow)
    
    def run_respawn(self):
        """Run respawn."""
        print("\n[1️⃣] RESPAWN\n")
        self.run_async(bot.respawn_character)
    
    def run_detect_buttons(self):
        """Run button detection."""
        print("\n[2️⃣] DETECT BUTTONS\n")
        self.run_async(bot.map_buttons)
    
    def run_process_needs(self):
        """Run process needs."""
        print("\n[3️⃣] PROCESS NEEDS\n")
        self.run_async(bot.process_needs)
    
    def stop_action(self):
        """Stop current action."""
        print("\n[⏹] STOP\n")
        print("[!] Setting STOP_FLAG...")
        bot.STOP_FLAG = True
        self.update_status("⏹ Stopping...")
        time.sleep(0.5)
        self.is_running = False
        self.enable_buttons()
        self.update_status("Stopped")


def main():
    root = tk.Tk()
    gui = AdoptMeGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
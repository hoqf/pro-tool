# Pro Tool's see-through app window.
#
# The page is drawn by Microsoft's WebView2 *composition* control inside a transparent WPF window, so wherever
# the page is transparent you see straight through to the desktop (used by the genie minimize effect). The
# older pywebview window can't do that: its web view always paints a solid layer behind the page.
#
# The page talks to the window through window.pywebview.api.* exactly as before; a small bridge script provides
# that object here, so the page doesn't need to know which window it's in. If anything here can't start, the
# caller falls back to the pywebview window.

import ctypes
import json
import os
import sys
import threading
import webbrowser
from ctypes import wintypes

MIN_W, MIN_H = 980, 640

# window.pywebview.api.<name>(...) -> a message to Python -> a reply that resolves the promise.
# Also: dragging the title bar (it starts once the mouse actually moves, so double-click still maximizes).
BRIDGE_JS = r"""
(() => {
  if (window.pywebview) return;
  const wv = window.chrome && window.chrome.webview; if (!wv) return;
  const pending = new Map(); let next = 0;
  const call = (name, args) => new Promise((resolve, reject) => {
    const id = ++next; pending.set(id, {resolve, reject});
    wv.postMessage(JSON.stringify({id, name, args}));
  });
  wv.addEventListener("message", e => {
    let m; try { m = typeof e.data === "string" ? JSON.parse(e.data) : e.data; } catch (x) { return; }
    const p = m && pending.get(m.id); if (!p) return;
    pending.delete(m.id); m.error ? p.reject(new Error(m.error)) : p.resolve(m.result);
  });
  window.pywebview = {api: new Proxy({}, {get: (_, name) => typeof name === "string" ? (...args) => call(name, args) : undefined})};
  window.__ptTransparent = true;                     // real see-through window: the genie needs no desktop picture
  let down = null;
  addEventListener("mousedown", e => {
    if (e.button !== 0 || !e.target.closest || !e.target.closest(".pywebview-drag-region") || e.target.closest("button")) { down = null; return; }
    down = {x: e.screenX, y: e.screenY};
  }, true);
  addEventListener("mousemove", e => {
    if (down && (e.buttons & 1) && Math.hypot(e.screenX - down.x, e.screenY - down.y) > 3) { down = null; call("drag", []); }
  }, true);
  addEventListener("mouseup", () => { down = null; }, true);
  const ready = () => dispatchEvent(new Event("pywebviewready"));
  document.readyState === "loading" ? addEventListener("DOMContentLoaded", ready) : ready();
})();
"""


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def _load_dotnet(webview_lib, wpf_dll):
    try:
        import clr                                       # pythonnet, as used by pywebview
    except Exception:
        os.environ["PYTHONNET_RUNTIME"] = "coreclr"
        import clr
    wpf, fx, draw = "31bf3856ad364e35", "b77a5c561934e089", "b03f5f7f11d50a3a"
    for name, tok in (("PresentationFramework", wpf), ("PresentationCore", wpf), ("WindowsBase", wpf),
                      ("System.Xaml", fx), ("System.Drawing", draw)):
        clr.AddReference(f"{name}, Version=4.0.0.0, Culture=neutral, PublicKeyToken={tok}")
    clr.AddReference(os.path.join(webview_lib, "Microsoft.Web.WebView2.Core.dll"))
    clr.AddReference(wpf_dll)


class WindowBridge:
    """What the page can ask the window to do (same names as the pywebview version's WindowApi)."""

    def __init__(self, host):
        self.h = host

    def minimize(self):
        self.h.let_min = True                      # ours (after the genie): let it straight through
        try:
            self.h.win.WindowState = self.h.WindowState.Minimized
        finally:
            self.h.let_min = False

    def toggle_maximize(self):
        self.h.toggle_maximize()

    def close(self):
        self.h.win.Close()

    def drag(self):
        if not self.h.maxed:
            try:
                self.h.win.DragMove()              # follows the mouse until the button is released
            except Exception:
                pass

    def resize_to(self, width, height, edge):
        h = self.h
        if h.maxed:
            return
        win = h.win
        right, bottom = win.Left + win.Width, win.Top + win.Height
        w, ht = max(MIN_W, float(width)), max(MIN_H, float(height))
        if "w" in edge:
            win.Left = right - w
        if "n" in edge:
            win.Top = bottom - ht
        win.Width, win.Height = w, ht

    HIT = {"w": 10, "e": 11, "n": 12, "nw": 13, "ne": 14, "s": 15, "sw": 16, "se": 17}   # HTLEFT ... HTBOTTOMRIGHT

    def begin_resize(self, edge):
        """Let Windows resize the window from this edge, exactly as if its border had been grabbed.
        Windows follows the mouse until the button is released, so the page doesn't have to."""
        h, hit = self.h, self.HIT.get(edge)
        if h.maxed or not hit:
            return False
        u = ctypes.windll.user32
        button = 0x02 if u.GetSystemMetrics(23) else 0x01          # left button (right if the buttons are swapped)
        if not u.GetAsyncKeyState(button) & 0x8000:
            return False                                            # already let go: nothing to resize
        from System.Windows.Input import Mouse
        Mouse.Capture(None)                                         # the page had the mouse; give it to Windows
        u.ReleaseCapture()
        pt = wintypes.POINT(); u.GetCursorPos(ctypes.byref(pt))
        u.SendMessageW(wintypes.HWND(h.hwnd), 0x00A1, hit, (pt.y & 0xFFFF) << 16 | (pt.x & 0xFFFF))   # WM_NCLBUTTONDOWN
        return True

    def hide_to_tray(self):
        tray = self.h.tray
        if not (tray and tray.ok):
            return False
        self.h.win.Hide()
        if not self.h.told_tray:
            self.h.told_tray = True
            try:
                tray.notifier.notify("Pro Tool is still running", "Click the Pro Tool icon by the clock to open it again.", sound=False)
            except Exception:
                pass
        return True

    def os_anim(self, enabled):
        """Windows' own minimize/restore animation off while the page's genie is on."""
        self.h.genie = not enabled
        flag = ctypes.c_int(0 if enabled else 1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(self.h.hwnd, 3, ctypes.byref(flag), ctypes.sizeof(flag))
        return True

    def backdrop(self):
        return ""                                  # not needed: the window really is see-through


class Host:
    def __init__(self, url, title, icon_path, data_dir, make_tray, on_closed):
        self.url, self.title, self.icon_path, self.data_dir = url, title, icon_path, data_dir
        self.make_tray, self.on_closed = make_tray, on_closed
        self.win = self.ctl = self.tray = None
        self.hwnd = 0
        self.maxed, self.saved = False, None
        self.genie, self.let_min, self.told_tray = False, False, False
        self.was_minimized = False
        self.api = WindowBridge(self)
        self.started = threading.Event()
        self.error = None
        self.page_ok = False
        self._procs = []

    # ---- helpers on the window's own thread
    def ui(self, fn):
        from System import Action
        self.win.Dispatcher.BeginInvoke(Action(fn))

    def run_js(self, code):
        def go():
            try:
                if self.ctl is not None and self.ctl.CoreWebView2 is not None:
                    self.ctl.CoreWebView2.ExecuteScriptAsync(code)
            except Exception:
                pass
        self.ui(go)

    def toggle_maximize(self):
        win = self.win
        if self.maxed:
            if self.saved:
                win.Left, win.Top, win.Width, win.Height = self.saved
            self.maxed = False
        else:
            self.saved = (win.Left, win.Top, win.Width, win.Height)
            u = ctypes.windll.user32
            mon = u.MonitorFromWindow(self.hwnd, 2)                      # MONITOR_DEFAULTTONEAREST
            mi = _MonitorInfo(); mi.cbSize = ctypes.sizeof(_MonitorInfo)
            u.GetMonitorInfoW(mon, ctypes.byref(mi))
            from System.Windows.Media import VisualTreeHelper
            dpi = VisualTreeHelper.GetDpi(win)
            sx, sy = dpi.DpiScaleX or 1.0, dpi.DpiScaleY or 1.0
            wa = mi.rcWork                                              # the screen minus the taskbar
            win.Left, win.Top = wa.left / sx, wa.top / sy
            win.Width, win.Height = (wa.right - wa.left) / sx, (wa.bottom - wa.top) / sy
            self.maxed = True
        self.run_js(f"document.documentElement.classList.toggle('maxed', {str(self.maxed).lower()})")

    def hook_taskbar(self):
        """Clicking the taskbar button (or Win+Down) minimizes; with the genie on, it plays first."""
        u = ctypes.windll.user32
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        u.SetWindowLongPtrW.restype = ctypes.c_void_p
        u.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.CallWindowProcW.restype = LRESULT
        hwnd = self.hwnd
        style = u.GetWindowLongW(hwnd, -16)
        if not style & 0x00020000:
            u.SetWindowLongW(hwnd, -16, style | 0x00020000 | 0x00080000)            # WS_MINIMIZEBOX | WS_SYSMENU (no frame)
        holder = {}

        def proc(h, msg, wp, lp):
            if msg == 0x0112 and (wp & 0xFFF0) == 0xF020:                           # WM_SYSCOMMAND, SC_MINIMIZE
                if self.let_min:
                    pass
                elif self.genie:
                    self.run_js("window.__genieOut ? window.__genieOut(() => pywebview.api.minimize()) : pywebview.api.minimize()")
                    return 0
            return u.CallWindowProcW(holder["old"], h, msg, wp, lp)

        cb = WNDPROC(proc)
        self._procs.append(cb)                                                        # keep it alive
        holder["old"] = u.SetWindowLongPtrW(hwnd, -4, ctypes.cast(cb, ctypes.c_void_p))

    def bring_back(self):                           # from the tray icon
        def go():
            self.win.Show()
            if self.win.WindowState == self.WindowState.Minimized:
                self.win.WindowState = self.WindowState.Normal
            self.win.Activate()
        self.ui(go)

    # ---- messages from the page
    def on_message(self, sender, e):
        try:
            m = json.loads(e.TryGetWebMessageAsString())
        except Exception:
            return
        mid, name, args = m.get("id"), m.get("name"), m.get("args") or []
        reply = {"id": mid}
        fn = getattr(self.api, name, None) if isinstance(name, str) and not name.startswith("_") else None
        if fn is None:
            reply["error"] = f"no such call: {name}"
        else:
            try:
                reply["result"] = fn(*args)
            except Exception as ex:
                reply["error"] = str(ex)
        try:
            self.ctl.CoreWebView2.PostWebMessageAsString(json.dumps(reply))
        except Exception:
            pass

    # ---- the window
    def build(self):
        from System import Uri
        from System.Windows import Application, Window, WindowStyle, ResizeMode, WindowState, WindowStartupLocation
        from System.Windows.Interop import WindowInteropHelper
        from System.Windows.Media import Brushes
        from System.Windows.Media.Imaging import BitmapFrame
        from System.Drawing import Color
        from Microsoft.Web.WebView2.Wpf import WebView2CompositionControl, CoreWebView2CreationProperties
        self.WindowState = WindowState

        app = Application()
        win = Window()
        win.Title = self.title
        win.WindowStyle = getattr(WindowStyle, "None")
        win.AllowsTransparency = True
        win.Background = Brushes.Transparent
        win.ResizeMode = ResizeMode.CanResize                         # keeps it minimizable from the taskbar
        win.Width, win.Height, win.MinWidth, win.MinHeight = 1440, 920, MIN_W, MIN_H
        win.WindowStartupLocation = WindowStartupLocation.CenterScreen
        try:
            win.Icon = BitmapFrame.Create(Uri(self.icon_path))
        except Exception:
            pass

        ctl = WebView2CompositionControl()
        ctl.DefaultBackgroundColor = Color.Transparent
        props = CoreWebView2CreationProperties()
        props.UserDataFolder = os.path.join(self.data_dir, "app-webview")
        # The page lives in a see-through (layered) window, which Chromium can wrongly judge as covered: it would
        # then stop drawing (black window) and pause animations (the genie never finishes). Never do that here.
        props.AdditionalBrowserArguments = ("--disable-features=CalculateNativeWinOcclusion "
                                            "--disable-backgrounding-occluded-windows --disable-renderer-backgrounding")
        ctl.CreationProperties = props
        win.Content = ctl
        self.win, self.ctl = win, ctl

        def ready(sender, e):
            if not e.IsSuccess:
                self.error = f"web view didn't start: {e.InitializationException}"
                self.started.set()
                win.Close()
                return
            core = ctl.CoreWebView2
            s = core.Settings
            s.AreDevToolsEnabled = False
            s.IsStatusBarEnabled = False
            s.IsZoomControlEnabled = False
            core.WebMessageReceived += self.on_message

            def new_window(s2, e2):                                   # links that open a new window: system browser
                e2.Handled = True
                try:
                    webbrowser.open(e2.Uri)
                except Exception:
                    pass
            core.NewWindowRequested += new_window
            core.AddScriptToExecuteOnDocumentCreatedAsync(BRIDGE_JS)
            core.Navigate(self.url)
            ctl.Focus()
            self.page_ok = True
            self.started.set()

        ctl.CoreWebView2InitializationCompleted += ready

        def loaded(s, e):
            self.hwnd = WindowInteropHelper(win).Handle.ToInt64()
            try:
                self.hook_taskbar()
            except Exception as ex:
                print(f"Couldn't set up taskbar minimize: {ex}")
            try:
                if self.make_tray:
                    self.tray = self.make_tray(self.bring_back, lambda: self.ui(win.Close))
            except Exception as ex:
                print(f"Couldn't set up the tray icon: {ex}")
            ctl.EnsureCoreWebView2Async(None)
        win.Loaded += loaded

        def state_changed(s, e):                                       # back from the taskbar: genie pours back out
            if win.WindowState == WindowState.Minimized:
                self.was_minimized = True
            elif self.was_minimized:
                self.was_minimized = False
                self.run_js("window.__genieIn && window.__genieIn()")
        win.StateChanged += state_changed

        def closed(s, e):
            try:
                if self.tray:
                    self.tray.close()
            finally:
                if self.on_closed:
                    self.on_closed()
        win.Closed += closed
        return app, win

    def run(self):
        from System.Threading import Thread, ThreadStart, ApartmentState

        def ui_thread():
            try:
                app, win = self.build()
                app.Run(win)
            except Exception as ex:
                self.error = self.error or f"window failed: {ex}"
                self.started.set()

        t = Thread(ThreadStart(ui_thread))
        t.SetApartmentState(ApartmentState.STA)
        t.Start()
        t.Join()


def run_window(url, title, icon_path, data_dir, webview_lib, wpf_dll, make_tray=None, on_shown=None):
    """Open the see-through window and block until it closes. Returns (True, None) when it ran, or
    (False, reason) when it couldn't start (the caller then uses the pywebview window instead)."""
    if not os.path.isfile(wpf_dll):
        return False, "see-through window library not found"
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)            # sharp on every monitor
    except Exception:
        pass
    try:
        _load_dotnet(webview_lib, wpf_dll)
        from Microsoft.Web.WebView2.Core import CoreWebView2Environment
        native = os.path.join(webview_lib, "runtimes", "win-x64" if sys.maxsize > 2 ** 32 else "win-x86", "native")
        if os.path.isdir(native):
            CoreWebView2Environment.SetLoaderDllFolderPath(native)
    except Exception as ex:
        return False, f".NET / WebView2 couldn't load: {ex}"
    host = Host(url, title, icon_path, data_dir, make_tray, None)
    if on_shown:
        threading.Thread(target=lambda: (host.started.wait(30), host.error is None and on_shown(host)), daemon=True).start()
    host.run()
    if not host.page_ok:                                          # never got as far as showing the page
        return False, host.error or "the see-through window didn't start"
    return True, None

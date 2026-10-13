"""VERONICA's hands on the PC (stage 4; owner 2026-10-14: everything — the screen, mouse and keyboard, apps and
windows, volume, media, lock, notifications — with a yes/no before each action while he is there, and allowed any time
for approved work when he is not, each action recorded).

Read-only actions (look_at_screen, list_windows) need no approval. Every action that changes something goes through
`gate()`: with the owner present it waits for his yes (in the window or in words); in a heartbeat run it runs at once
and is recorded, unless the owner switched her hands off ("stop her hands", the window's button).

Everything Windows is behind `Win32`; the tests pass a fake that records calls and never touches the real PC.
"""
import ctypes
import os
import subprocess
import sys
from pathlib import Path

KEYS = {'enter': 0x0D, 'return': 0x0D, 'tab': 0x09, 'escape': 0x1B, 'esc': 0x1B, 'space': 0x20, 'backspace': 0x08,
        'delete': 0x2E, 'del': 0x2E, 'insert': 0x2D, 'home': 0x24, 'end': 0x23, 'pageup': 0x21, 'pagedown': 0x22,
        'up': 0x26, 'down': 0x28, 'left': 0x25, 'right': 0x27, 'ctrl': 0x11, 'control': 0x11, 'alt': 0x12,
        'shift': 0x10, 'win': 0x5B, 'capslock': 0x14, 'printscreen': 0x2C}
KEYS.update({f'f{i}': 0x6F + i for i in range(1, 13)})
MODIFIERS = {'ctrl': 0x11, 'alt': 0x12, 'shift': 0x10, 'win': 0x5B}
MEDIA = {'play_pause': 0xB3, 'stop': 0xB2, 'next': 0xB0, 'previous': 0xB1}
VOLUME = {'up': 0xAF, 'down': 0xAE, 'mute': 0xAD}

# name: (what it does, the parameters, True when the owner's yes is needed first)
ACTIONS = {
    'look_at_screen': ('Take a screenshot and answer a question about what is on the screen.',
                       {'question': (str, True, 'What to look for or answer')}, False),
    'list_windows': ('List the open windows and their titles.', {}, False),
    'click': ('Click the mouse at a point on the screen.', {'x': (int, True, 'Pixels from the left'),
                                                            'y': (int, True, 'Pixels from the top'),
                                                            'button': (str, False, 'left or right (default left)')}, True),
    'type_text': ('Type text with the keyboard, as if at the focused window.',
                  {'text': (str, True, 'The text to type')}, True),
    'press_keys': ('Press keys, e.g. "ctrl+s" or "enter" or "alt+tab".',
                   {'keys': (str, True, 'Keys joined with +')}, True),
    'scroll': ('Scroll the wheel.', {'direction': (str, True, 'up or down'),
                                     'amount': (int, False, 'Notches (default 3)')}, True),
    'activate_window': ('Bring an open window to the front.', {'title': (str, True, 'Part of its title')}, True),
    'minimize_window': ('Minimize an open window.', {'title': (str, True, 'Part of its title')}, True),
    'close_window': ('Ask an open window to close (as Alt+F4).', {'title': (str, True, 'Part of its title')}, True),
    'open_thing': ('Open a file, folder, web address or system page (e.g. ms-settings:sound).',
                   {'path_or_url': (str, True, 'Path or address')}, True),
    'set_volume': ('Change the system volume.', {'action': (str, True, 'up, down or mute')}, True),
    'media_key': ('Play, pause or skip what is playing.', {'action': (str, True, 'play_pause, stop, next or previous')}, True),
    'lock_screen': ('Lock the PC (the owner signs in again).', {}, True),
    'notify_owner': ('Show the owner a notification with words.', {'text': (str, True, 'The words')}, False),
}
NEEDS_YES = tuple(name for name, spec in ACTIONS.items() if spec[2])


class HandsError(RuntimeError):
    pass


class HandsStopped(HandsError):
    """The owner switched her hands off."""


def _find_window(backend, title):
    wanted = ' '.join(str(title).lower().split())
    found = [w for w in backend.windows() if wanted in w['title'].lower()]
    if not found:
        raise HandsError(f'There is no open window with "{title}" in its title.')
    return found[0]


def run(name, given, backend):
    """Do one action. `backend` is Win32 (the real PC) or a fake."""
    if name not in ACTIONS:
        raise HandsError(f'There is no PC action called {name}.')
    if name == 'list_windows':
        rows = backend.windows()
        return '\n'.join(f'{w["handle"]}  {w["title"]}' for w in rows) or 'No windows are open.'
    if name == 'click':
        button = str(given.get('button', 'left')).lower()
        if button not in ('left', 'right'):
            raise HandsError('The button is left or right.')
        backend.click(int(given['x']), int(given['y']), button)
        return f'Clicked {button} at ({given["x"]}, {given["y"]}).'
    if name == 'type_text':
        text = str(given['text'])
        backend.type_text(text)
        return f'Typed {len(text)} characters.'
    if name == 'press_keys':
        keys = [k.strip().lower() for k in str(given['keys']).split('+')]
        for key in keys:
            if key not in KEYS and not (len(key) == 1 and key.isalnum() or key in '.,;\'[]\\/-=`'):
                raise HandsError(f'I do not know the key "{key}".')
        backend.press(keys)
        return f'Pressed {given["keys"]}.'
    if name == 'scroll':
        direction = str(given.get('direction', 'down')).lower()
        if direction not in ('up', 'down'):
            raise HandsError('The direction is up or down.')
        amount = max(1, min(50, int(given.get('amount', 3))))
        backend.scroll(direction, amount)
        return f'Scrolled {direction} {amount} notch(es).'
    if name in ('activate_window', 'minimize_window', 'close_window'):
        window = _find_window(backend, given['title'])
        getattr(backend, {'activate_window': 'activate', 'minimize_window': 'minimize',
                          'close_window': 'close'}[name])(window['handle'])
        verb = {'activate_window': 'Brought to the front', 'minimize_window': 'Minimized',
                'close_window': 'Asked to close'}[name]
        return f'{verb}: {window["title"]}.'
    if name == 'open_thing':
        target = str(given['path_or_url']).strip().strip('"')
        if not target or any(c in target for c in '\r\n'):
            raise HandsError('Give a path or address to open.')
        backend.open(target)
        return f'Opened {target}.'
    if name == 'set_volume':
        action = str(given['action']).lower()
        if action not in VOLUME:
            raise HandsError('The action is up, down or mute.')
        backend.key(VOLUME[action])
        return f'Volume: {action}.'
    if name == 'media_key':
        action = str(given['action']).lower()
        if action not in MEDIA:
            raise HandsError('The action is play_pause, stop, next or previous.')
        backend.key(MEDIA[action])
        return f'Media: {action}.'
    if name == 'lock_screen':
        backend.lock()
        return 'The PC is locked.'
    if name == 'notify_owner':
        backend.notify(str(given['text']))
        return 'Shown to the owner.'
    raise HandsError(f'{name} is not something I can do.')


def words(name, given):
    """The action in words, for the owner's yes/no and the record."""
    if name == 'click':
        return f'click {given.get("button", "left")} at ({given.get("x")}, {given.get("y")})'
    if name == 'type_text':
        return f'type: {str(given.get("text"))[:120]!r}'
    if name in ('press_keys', 'scroll', 'activate_window', 'minimize_window', 'close_window', 'open_thing',
                'set_volume', 'media_key', 'notify_owner'):
        value = given.get('keys') or given.get('direction') or given.get('title') or given.get('path_or_url') \
            or given.get('action') or given.get('text')
        return f'{name.replace("_", " ")}: {str(value)[:160]}'
    if name == 'lock_screen':
        return 'lock the PC'
    return name


class Win32:
    """The real PC. Nothing here runs in the tests."""

    def shot(self):
        from launcher.build import png
        import numpy as np
        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        width, height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        screen = gdi32.GetDC(0)
        memory = gdi32.CreateCompatibleDC(screen)
        bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
        gdi32.SelectObject(memory, bitmap)
        gdi32.BitBlt(memory, 0, 0, width, height, screen, 0, 0, 0x00CC0020)   # SRCCOPY
        class Info(ctypes.Structure):
            _fields_ = [('size', ctypes.c_uint), ('width', ctypes.c_long), ('height', ctypes.c_long),
                        ('planes', ctypes.c_ushort), ('bits', ctypes.c_ushort), ('compression', ctypes.c_uint),
                        ('image_size', ctypes.c_uint), ('x', ctypes.c_long), ('y', ctypes.c_long),
                        ('colors', ctypes.c_uint), ('important', ctypes.c_uint)]
        info = Info(40, width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        pixels = np.empty((height, width, 4), dtype=np.uint8)
        gdi32.GetDIBits(memory, bitmap, 0, height, pixels.ctypes.data, ctypes.byref(info), 0)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        gdi32.ReleaseDC(0, screen)
        return png(pixels[:, :, [2, 1, 0, 3]])

    def windows(self):
        user32 = ctypes.windll.user32
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def each(handle, _):
            if user32.IsWindowVisible(handle):
                length = user32.GetWindowTextLengthW(handle)
                if length:
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(handle, buffer, length + 1)
                    found.append({'handle': handle, 'title': buffer.value})
            return True
        user32.EnumWindows(each, 0)
        return found

    def _input(self, kind, **fields):
        class Mouse(ctypes.Structure):
            _fields_ = [('dx', ctypes.c_long), ('dy', ctypes.c_long), ('data', ctypes.c_ulong),
                        ('flags', ctypes.c_ulong), ('time', ctypes.c_ulong), ('extra', ctypes.c_void_p)]
        class Key(ctypes.Structure):
            _fields_ = [('vk', ctypes.c_ushort), ('scan', ctypes.c_ushort), ('flags', ctypes.c_ulong),
                        ('time', ctypes.c_ulong), ('extra', ctypes.c_void_p)]
        class Union(ctypes.Union):
            _fields_ = [('mouse', Mouse), ('key', Key), ('pad', ctypes.c_byte * 32)]
        class Input(ctypes.Structure):
            _fields_ = [('type', ctypes.c_ulong), ('u', Union)]
        inputs = []
        for values in fields['list']:
            item = Input(kind)
            if kind == 0:
                item.u.mouse = Mouse(*values)
            else:
                item.u.key = Key(*values)
            inputs.append(item)
        array = (Input * len(inputs))(*inputs)
        ctypes.windll.user32.SendInput(len(inputs), array, ctypes.sizeof(Input))

    def click(self, x, y, button):
        user32 = ctypes.windll.user32
        width, height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        down, up = (0x0002, 0x0004) if button == 'left' else (0x0008, 0x0010)
        self._input(0, list=[(int(x * 65535 / width), int(y * 65535 / height), 0, 0x0001 | 0x8000, 0, None),
                             (0, 0, 0, down, 0, None), (0, 0, 0, up, 0, None)])

    def scroll(self, direction, amount):
        self._input(0, list=[(0, 0, (120 * amount) & 0xFFFFFFFF if direction == 'up' else (-120 * amount) & 0xFFFFFFFF,
                              0x0800, 0, None)])

    def key(self, vk, flags=0):
        self._input(1, list=[(vk, 0, flags, 0, None), (vk, 0, flags | 0x0002, 0, None)])

    def press(self, keys):
        held = [MODIFIERS[k] for k in keys if k in MODIFIERS]
        for vk in held:
            self._input(1, list=[(vk, 0, 0, 0, None)])
        for name in [k for k in keys if k not in MODIFIERS]:
            vk = KEYS.get(name) or ord(name.upper())
            self._input(1, list=[(vk, 0, 0, 0, None), (vk, 0, 0x0002, 0, None)])
        for vk in reversed(held):
            self._input(1, list=[(vk, 0, 0x0002, 0, None)])

    def type_text(self, text):
        for char in text:
            code = ord(char)
            self._input(1, list=[(code, 0, 0x0004, 0, None), (code, 0, 0x0004 | 0x0002, 0, None)])

    def activate(self, handle):
        user32 = ctypes.windll.user32
        user32.ShowWindow(handle, 9)          # SW_RESTORE
        user32.SetForegroundWindow(handle)

    def minimize(self, handle):
        ctypes.windll.user32.ShowWindow(handle, 6)   # SW_MINIMIZE

    def close(self, handle):
        ctypes.windll.user32.PostMessageW(handle, 0x0010, 0, 0)   # WM_CLOSE

    def open(self, target):
        os.startfile(target)   # noqa: S606 (the owner approved this exact action)

    def lock(self):
        ctypes.windll.user32.LockWorkStation()

    def notify(self, text):
        script = (
            '[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;'
            '$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);'
            '$n = $t.GetElementsByTagName("text"); $n.Item(0).AppendChild($t.CreateTextNode("Veronica")) | Out-Null;'
            f'$n.Item(1).AppendChild($t.CreateTextNode({text!r})) | Out-Null;'
            '[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Veronica").Show('
            '[Windows.UI.Notifications.ToastNotification]::new($t))')
        subprocess.run(['powershell.exe', '-NoProfile', '-Command', script], capture_output=True, timeout=30,
                       creationflags=0x08000000 if sys.platform == 'win32' else 0)


class Fake:
    """Records calls; never touches the PC."""

    def __init__(self, windows=(), shot=b''):
        self.calls, self._windows, self._shot = [], list(windows), shot
        self.files = []

    def shot(self):
        return self._shot

    def windows(self):
        return list(self._windows)

    def click(self, x, y, button):
        self.calls.append(('click', x, y, button))

    def scroll(self, direction, amount):
        self.calls.append(('scroll', direction, amount))

    def key(self, vk):
        self.calls.append(('key', vk))

    def press(self, keys):
        self.calls.append(('press', tuple(keys)))

    def type_text(self, text):
        self.calls.append(('type', text))

    def activate(self, handle):
        self.calls.append(('activate', handle))

    def minimize(self, handle):
        self.calls.append(('minimize', handle))

    def close(self, handle):
        self.calls.append(('close', handle))

    def open(self, target):
        self.files.append(target)
        self.calls.append(('open', target))

    def lock(self):
        self.calls.append(('lock',))

    def notify(self, text):
        self.calls.append(('notify', text))

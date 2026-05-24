import os, sys
if hasattr(sys, '_MEIPASS'):
    win32_path = os.path.join(sys._MEIPASS, 'win32')
    if win32_path not in sys.path:
        sys.path.insert(0, win32_path)
    if sys._MEIPASS not in sys.path:
        sys.path.insert(0, sys._MEIPASS)
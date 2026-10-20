"""Shared frozen entry point: no arguments opens GUI; commands use CLI."""
import sys

from wasdify.cli import main as cli_main


def _restore_cli_streams() -> None:
    """Recover CLI output in a windowed EXE without creating a console."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream is not None and not stream.isatty():
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    import ctypes
    from ctypes import wintypes
    import msvcrt
    import os

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.DuplicateHandle.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
    ]
    kernel.DuplicateHandle.restype = wintypes.BOOL
    kernel.AttachConsole.argtypes = [wintypes.DWORD]
    kernel.AttachConsole.restype = wintypes.BOOL
    kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetConsoleMode.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL

    process = kernel.GetCurrentProcess()

    def duplicate(handle: int | None) -> int | None:
        copied = wintypes.HANDLE()
        if handle and kernel.DuplicateHandle(process, handle, process,
                                             ctypes.byref(copied), 0, False, 2):
            return copied.value
        return None

    # AttachConsole can replace standard handles. Preserve inherited pipes/files
    # first, then attach only to an existing console; never call AllocConsole.
    missing = [(name, number) for name, number in (("stdout", -11), ("stderr", -12))
               if getattr(sys, name) is None]
    handles = {name: duplicate(kernel.GetStdHandle(number)) for name, number in missing}
    if any(handle is None for handle in handles.values()):
        kernel.AttachConsole(0xFFFFFFFF)
    for name, number in missing:
        handle = handles[name]
        if handle is None:
            handle = duplicate(kernel.GetStdHandle(number))
        if handle is None:
            stream = open(os.devnull, "w", encoding="utf-8")
        else:
            mode = wintypes.DWORD()
            if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel.CloseHandle(handle)
                # Python's Windows console IO writes Unicode, independent of
                # the terminal's code page.
                stream = open("CONOUT$", "w", encoding="utf-8", buffering=1)
            else:
                fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
                stream = open(fd, "w", encoding="utf-8", errors="backslashreplace", buffering=1)
        setattr(sys, name, stream)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv:
        _restore_cli_streams()
    return cli_main(argv or ["gui"])


if __name__ == "__main__":
    raise SystemExit(main())

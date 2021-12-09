"""
"""
import os
import warnings
from pathlib import Path
from typing import Tuple, Union
from logging import getLogger

from win32com.client import Dispatch

from .base import Menu, MenuItem
from ..utils import WinLex

# TODO: Reimplement/port to get rid of _legacy
from .._legacy.win32 import folder_path


log = getLogger(__name__)


class WindowsMenu(Menu):
    def create(self):
        # TODO: Check if elevated permissions are needed
        log.debug("Creating %s", self.start_menu_location)
        self.start_menu_location.mkdir(parents=True, exist_ok=True)
        return (self.start_menu_location,)

    def remove(self):
        # TODO: Check if elevated permissions are needed
        log.debug("Removing %s", self.start_menu_location)
        self.start_menu_location.rmdir()
        return (self.start_menu_location,)

    @property
    def start_menu_location(self):
        """
        On Windows we can create shortcuts in several places:

        - Start Menu
        - Desktop
        - Quick launch (only for user installs)

        In this property we only report the path to the Start menu.
        For other menus, check their respective properties.
        """
        return Path(folder_path(self.mode, False, "start")) / self.name

    @property
    def quick_launch_location(self):
        if self.mode == "system":
            warnings.warn("Quick launch menus are not available for system level installs")
            return
        return Path(folder_path(self.mode, False, "quicklaunch"))

    @property
    def desktop_location(self):
        return Path(folder_path(self.mode, False, "desktop"))

    @property
    def placeholders(self):
        placeholders = super().placeholders
        placeholders.update(
            {
                "SCRIPTS_DIR": str(self.prefix / "Scripts"),
                "PYTHON": str(self.prefix / "python.exe"),
                "PYTHONW": str(self.prefix / "pythonw.exe"),
                "BASE_PYTHON": str(self.base_prefix / "python.exe"),
                "BASE_PYTHONW": str(self.base_prefix / "pythonw.exe"),
                "BIN_DIR": str(self.prefix / "Library" / "bin"),
                "SP_DIR": str(self._site_packages()),
                "ICON_EXT": "ico",
            }
        )
        return placeholders

    def _conda_exe_path_candidates(self):
        return (
            self.base_prefix / "_conda.exe",
            Path(os.environ.get("CONDA_EXE", r"C:\oops\a_file_that_does_not_exist")),
            self.base_prefix / "condabin" / "conda.bat",
            self.base_prefix / "bin" / "conda.bat",
            Path(os.environ.get("MAMBA_EXE", r"C:\oops\a_file_that_does_not_exist")),
            self.base_prefix / "condabin" / "micromamba.exe",
            self.base_prefix / "bin" / "micromamba.exe",
        )

    def render(self, value: Union[str, None], slug: bool = False):
        """
        We extend the render method here to replace forward slashes with backslashes.
        We ONLY do it if the string does not start with /, because it might
        be just a windows-style command-line flag (e.g. cmd /K something).

        This will not escape strings such as `/flag:something`, common
        in compiler stuff but we can assume such windows specific
        constructs will have their platform override, which is always an option.

        This is just a convenience for things like icon paths or Unix-like paths
        in the command key.
        """
        value = super().render(value, slug=slug)
        if value is None:
            return value
        if "/" in value and value[0] != "/":
            value = value.replace("/", "\\")
        return value

    def _site_packages(self, prefix=None):
        if prefix is None:
            prefix = self.prefix
        return self.prefix / "Lib" / "site-packages"

    def _paths(self):
        return (self.start_menu_location,)


class WindowsMenuItem(MenuItem):
    def create(self) -> Tuple[Path]:
        shell = Dispatch("WScript.Shell")
        activate = self.metadata.activate

        if activate:
            script = self._write_script()
        paths = self._paths()

        for path in paths:
            if not path.suffix == ".lnk":
                continue

            shortcut = shell.CreateShortCut(str(path))

            if activate:
                if self.metadata.no_console:
                    command = [
                        "powershell",
                        f'"start \'{script}\' -WindowStyle hidden"',
                    ]
                else:
                    command = [
                        "cmd",
                        "/K",
                        str(script),
                    ]
            else:
                command = self.render("command")

            target_path, *arguments = WinLex.quote_args(command)

            shortcut.Targetpath = target_path
            if arguments:
                shortcut.Arguments = " ".join(arguments)

            working_dir = self.render("working_dir")
            if working_dir:
                Path(working_dir).mkdir(parents=True, exist_ok=True)
            else:
                working_dir = "%HOMEPATH%"
            shortcut.WorkingDirectory = working_dir

            icon = self.render("icon")
            if icon:
                shortcut.IconLocation = icon

            # TODO: Check if elevated permissions are needed
            shortcut.save()
        return paths

    def remove(self) -> Tuple[Path]:
        paths = self._paths()
        for path in paths:
            # TODO: Check if elevated permissions are needed
            log.debug("Removing %s", path)
            os.unlink(path)
        return paths

    def _paths(self):
        directories = [self.menu.start_menu_location]
        if self.metadata.desktop:
            directories.append(self.menu.desktop_location)
        if self.metadata.quicklaunch and self.menu.quick_launch_location:
            directories.append(self.menu.quick_launch_location)

        # These are the different lnk files
        shortcuts = [directory / self._shortcut_filename() for directory in directories]

        if self.metadata.activate:
            # This is the accessory launcher script for cmd
            shortcuts.append(self._path_for_script())

        return tuple(shortcuts)

    def _shortcut_filename(self, ext="lnk"):
        env_suffix = f" ({self.menu.env_name})" if self.menu.env_name else ""
        return f"{self.render('name')}{env_suffix}.{ext}"

    def _path_for_script(self):
        return Path(self.menu.placeholders["MENU_DIR"]) / self._shortcut_filename("bat")

    def _command(self):
        lines = ["@ECHO OFF"]
        if self.metadata.activate:
            conda_exe = self.menu.conda_exe
            if self.menu._is_micromamba(conda_exe):
                activate = "shell activate"
            else:
                activate = "shell.cmd.exe activate"
            activator = f'{self.menu.conda_exe} {activate} "{self.menu.prefix}"'
            lines += [
                "@SETLOCAL ENABLEDELAYEDEXPANSION",
                f'@FOR /F "usebackq tokens=*" %%i IN (`{activator}`) do set "ACTIVATOR=%%i"',
                '@CALL %ACTIVATOR%',
                ":: This below is the user command"
            ]

        lines.append(" ".join(WinLex.quote_args(self.render("command"))))

        return "\r\n".join(lines)

    def _write_script(self, script_path=None):
        """
        This method generates the batch script that will be called by the shortcut
        """
        if script_path is None:
            script_path = self._path_for_script()
        else:
            script_path = Path(script_path)

        script_path.parent.mkdir(parents=True, exist_ok=True)
        with open(script_path, "w") as f:
            f.write(self._command())

        return script_path

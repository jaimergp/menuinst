"""
"""
import shutil
from pathlib import Path
import plistlib
import os
import shlex
from typing import Tuple
from tempfile import mkdtemp

from .base import Menu, MenuItem, _site_packages_in_unix


class MacOSMenu(Menu):
    def create(self):
        pass

    def remove(self):
        pass

    @property
    def placeholders(self):
        placeholders = super().placeholders
        placeholders.update(
            {
                "SP_DIR": str(_site_packages_in_unix()),
                "ICON_EXT": "icns",
                "PYTHONAPP": str(
                    Path(self.prefix) / "python.app" / "Contents" / "MacOS" / "python"
                ),
            }
        )
        return placeholders


class MacOSMenuItem(MenuItem):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        name = f"{self.render('name')}.app"
        if os.environ.get("PYTEST_IN_USE"):
            base = Path(mkdtemp(prefix="menuinst-pytest-"))
        elif self.menu.mode == "user":
            base = Path("~").expanduser()
        else:
            base = Path("/")

        self.location = base / "Applications" / name

    def create(self) -> Tuple[Path]:
        self._create_application_tree()
        icon = self.render("icon")
        if icon:
            shutil.copy(self.render("icon"), self.location / "Contents" / "Resources")
        self._write_pkginfo()
        self._write_plistinfo()
        self._write_script()
        return (self.location,)

    def remove(self) -> Tuple[Path]:
        shutil.rmtree(self.location)
        return (self.location,)

    def _create_application_tree(self):
        paths = [
            self.location / "Contents" / "Resources",
            self.location / "Contents" / "MacOS",
        ]
        for path in paths:
            path.mkdir(parents=True, exist_ok=False)
        return paths

    def _write_pkginfo(self):
        with open(self.location / "Contents" / "PkgInfo", "w") as f:
            f.write(f"APPL{self.render('name', slug=True)[:8]}")

    def _write_plistinfo(self):
        name = self.render("name", slug=True)
        pl = {
            "CFBundleExecutable": name,
            "CFBundleGetInfoString": f"{name}-1.0.0",
            "CFBundleIdentifier": f"com.{name}",
            "CFBundlePackageType": "APPL",
            "CFBundleVersion": "1.0.0",
            "CFBundleShortVersionString": "1.0.0",
        }
        icon = self.render("icon")
        if icon:
            pl["CFBundleIconFile"] = Path(icon).name

        with open(self.location / "Contents" / "Info.plist", "wb") as f:
            plistlib.dump(pl, f)

    def _write_script(self):
        lines = ["#!/bin/bash"]

        working_dir = self.render("working_dir")
        if working_dir:
            Path(working_dir).mkdir(parents=True, exist_ok=True)
            lines.append(f'cd "{working_dir}"')

        if self.render("activate"):
            lines.append(
                f"eval $(\"{self.menu.placeholders['BASE_PREFIX']}/_conda.exe\" "
                f"shell.bash activate \"{self.menu.placeholders['PREFIX']}\")"
            )

        lines.append(" ".join([shlex.quote(s) for s in self.render("command")]))

        script_path = self.location / "Contents" / "MacOS" / self.render("name", slug=True)
        with open(script_path, "w") as f:
            f.write("\n".join(lines))

        os.chmod(script_path, 0o755)
        return script_path
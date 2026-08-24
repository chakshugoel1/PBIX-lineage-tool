"""Single source of truth for the app version, used by the GUI title bar,
the PyInstaller build, the Inno Setup installer, and the auto-updater's
comparison against GitHub release tags (which must be "v" + this value,
e.g. "v1.0.0")."""
__version__ = "1.1.4"

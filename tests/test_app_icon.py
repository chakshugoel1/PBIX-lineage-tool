import os

from gui import main_window


def test_application_icon_file_is_available():
    assert os.path.isfile(main_window._APP_ICON_PATH)
    assert main_window._APP_ICON_PATH.endswith("Icon.ico")


def test_shortcut_icon_file_is_available():
    assert os.path.isfile(main_window._APP_ICON_PATH)
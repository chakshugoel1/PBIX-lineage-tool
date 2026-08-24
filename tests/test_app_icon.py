import os

from gui import main_window


def test_application_icon_file_is_available():
    assert os.path.isfile(main_window._APP_ICON_PATH)
    assert main_window._APP_ICON_PATH.endswith("Icon.png")


def test_shortcut_icon_file_is_available():
    icon_path = os.path.join(os.path.dirname(main_window._APP_ICON_PATH), "Icon.ico")
    assert os.path.isfile(icon_path)
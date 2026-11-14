import os
from pathlib import Path
from urllib.parse import urlsplit

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")

from gi.repository import Gdk, Gio, GLib, Gtk, WebKit

from navigation import MAX_ADDRESS_LENGTH, resolve_address
from storage import BrowserStorage, StorageError


APP_ID = "com.zaod.zbrowser"
HOME_URI = "https://www.google.com/"
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024


class BrowserApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_activate(self):
        if not self.get_windows():
            try:
                BrowserWindow(self).present()
            except (StorageError, GLib.Error) as error:
                window = Gtk.ApplicationWindow(application=self)
                window.set_title("Z Browser - Startup error")
                window.set_default_size(520, 160)
                message = Gtk.Label(label=f"Z Browser could not start: {error}")
                message.set_wrap(True)
                message.set_margin_start(20)
                message.set_margin_end(20)
                message.set_margin_top(20)
                message.set_margin_bottom(20)
                window.set_child(message)
                window.present()


class BrowserWindow(Gtk.ApplicationWindow):
    def __init__(self, application, private: bool = False):
        super().__init__(application=application)
        self.app = application
        self.private = private
        self.storage = BrowserStorage()
        self._status_serial = 0
        self._download_paths: dict[int, Path] = {}
        self._oversized_downloads: set[int] = set()
        self.context = self._create_context()
        self.context.connect("download-started", self._on_download_started)

        self.set_title("Z Browser - Private" if private else "Z Browser")
        self.set_default_size(1100, 740)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.append(self._build_toolbar())

        self.progress = Gtk.ProgressBar()
        self.progress.set_visible(False)
        root.append(self.progress)

        self.status = Gtk.Label(xalign=0)
        self.status.set_margin_start(10)
        self.status.set_margin_end(10)
        self.status.set_margin_top(3)
        self.status.set_margin_bottom(3)
        root.append(self.status)

        self.tabs = Gtk.Notebook()
        self.tabs.set_scrollable(True)
        self.tabs.set_vexpand(True)
        self.tabs.connect("switch-page", self._on_tab_switched)
        root.append(self.tabs)
        self.set_child(root)
        self._install_shortcuts()
        self.new_tab(HOME_URI)

    def _create_context(self):
        if self.private:
            manager = WebKit.WebsiteDataManager.new_ephemeral()
        else:
            self.storage.prepare_profile()
            manager = WebKit.WebsiteDataManager(
                base_data_directory=str(self.storage.webkit_data_directory),
                base_cache_directory=str(self.storage.webkit_cache_directory),
            )
        return WebKit.WebContext.new_with_website_data_manager(manager)

    def _build_toolbar(self) -> Gtk.Widget:
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        toolbar.set_margin_start(8)
        toolbar.set_margin_end(8)
        toolbar.set_margin_top(8)
        toolbar.set_margin_bottom(8)

        self.back_button = self._button("Back", self._go_back)
        self.forward_button = self._button("Forward", self._go_forward)
        self.reload_button = self._button("Reload", self._reload)
        toolbar.append(self.back_button)
        toolbar.append(self.forward_button)
        toolbar.append(self.reload_button)
        toolbar.append(self._button("Home", lambda _button: self.navigate(HOME_URI)))

        self.address = Gtk.Entry()
        self.address.set_hexpand(True)
        self.address.set_placeholder_text("Search Google or enter a web address")
        self.address.connect("activate", self._on_address_activate)
        toolbar.append(self.address)

        self.security_label = Gtk.Label(label="")
        toolbar.append(self.security_label)
        toolbar.append(self._button("Bookmark", self._toggle_bookmark))
        toolbar.append(self._button("+ Tab", lambda _button: self.new_tab(HOME_URI)))
        toolbar.append(self._build_menu())
        return toolbar

    @staticmethod
    def _button(label: str, callback) -> Gtk.Button:
        button = Gtk.Button(label=label)
        button.connect("clicked", callback)
        return button

    def _build_menu(self) -> Gtk.MenuButton:
        menu_button = Gtk.MenuButton(label="Menu")
        popover = Gtk.Popover()
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        menu.set_margin_top(6)
        menu.set_margin_bottom(6)
        menu.set_margin_start(6)
        menu.set_margin_end(6)
        actions = (
            ("New private window", self._open_private_window),
            ("Bookmarks", self._open_bookmarks),
            ("History", self._open_history),
            ("Downloads", self._open_downloads),
            ("About Z Browser", self._show_about),
        )
        for label, callback in actions:
            button = Gtk.Button(label=label)
            button.connect("clicked", self._run_menu_action, popover, callback)
            menu.append(button)
        popover.set_child(menu)
        menu_button.set_popover(popover)
        return menu_button

    @staticmethod
    def _run_menu_action(_button, popover, callback):
        popover.popdown()
        callback()

    def _install_shortcuts(self) -> None:
        controller = Gtk.EventControllerKey()
        controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(controller)

    def _on_key_pressed(self, _controller, keyval, _keycode, state):
        control = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if not control:
            return False
        key = (Gdk.keyval_name(keyval) or "").lower()
        if key == "l":
            self.address.grab_focus()
            self.address.select_region(0, -1)
        elif key == "t":
            self.new_tab(HOME_URI)
        elif key == "w":
            self._close_current_tab()
        elif key == "r":
            self._reload(None)
        elif key == "tab":
            page_count = self.tabs.get_n_pages()
            if page_count:
                current = self.tabs.get_current_page()
                self.tabs.set_current_page((current + (-1 if shift else 1)) % page_count)
        elif key == "n" and shift:
            self._open_private_window()
        else:
            return False
        return True

    def new_tab(self, uri: str | None = None) -> WebKit.WebView:
        view = WebKit.WebView.new_with_context(self.context)
        view.set_hexpand(True)
        view.set_vexpand(True)
        view.connect("load-changed", self._on_load_changed)
        view.connect("notify::title", self._on_title_changed)
        view.connect("notify::uri", self._on_uri_changed)
        view.connect("notify::estimated-load-progress", self._on_progress_changed)
        view.connect("create", self._on_create_view)
        view.connect("decide-policy", self._on_decide_policy)
        view.connect("permission-request", self._on_permission_request)
        view.connect("load-failed", self._on_load_failed)
        view.connect("load-failed-with-tls-errors", self._on_tls_error)

        tab_label = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        title = Gtk.Label(label="New Tab")
        title.set_max_width_chars(22)
        tab_label.append(title)
        close_button = Gtk.Button(label="x")
        close_button.set_has_frame(False)
        close_button.set_tooltip_text("Close tab")
        close_button.connect("clicked", lambda _button, page=view: self._close_tab(page))
        tab_label.append(close_button)

        page_number = self.tabs.append_page(view, tab_label)
        self.tabs.set_tab_reorderable(view, True)
        self.tabs.set_current_page(page_number)
        if uri:
            view.load_uri(uri)
        self._refresh_controls()
        return view

    def current_view(self) -> WebKit.WebView | None:
        page = self.tabs.get_nth_page(self.tabs.get_current_page())
        return page if isinstance(page, WebKit.WebView) else None

    def navigate(self, value: str) -> None:
        try:
            uri = resolve_address(value)
        except ValueError as error:
            self.show_status(str(error))
            return
        view = self.current_view()
        if view is not None:
            view.load_uri(uri)

    def _on_address_activate(self, entry) -> None:
        self.navigate(entry.get_text())

    def _go_back(self, _button) -> None:
        view = self.current_view()
        if view is not None and view.can_go_back():
            view.go_back()

    def _go_forward(self, _button) -> None:
        view = self.current_view()
        if view is not None and view.can_go_forward():
            view.go_forward()

    def _reload(self, _button) -> None:
        view = self.current_view()
        if view is not None:
            view.reload()

    def _on_tab_switched(self, _notebook, _page, _page_number) -> None:
        self._refresh_controls()

    def _on_load_changed(self, view, event) -> None:
        if event == WebKit.LoadEvent.STARTED and view == self.current_view():
            self.progress.set_fraction(0.0)
            self.progress.set_visible(True)
            self.show_status("Loading page...")
        elif event == WebKit.LoadEvent.FINISHED:
            if view == self.current_view():
                self.progress.set_visible(False)
                self._refresh_controls()
            uri = view.get_uri() or ""
            if not self.private and urlsplit(uri).scheme in ("http", "https"):
                try:
                    self.storage.record_visit(view.get_title() or uri, uri)
                except StorageError as error:
                    self.show_status(str(error))

    def _on_progress_changed(self, view, _property) -> None:
        if view == self.current_view():
            self.progress.set_fraction(view.get_estimated_load_progress())

    def _on_uri_changed(self, view, _property) -> None:
        if view == self.current_view():
            self._refresh_controls()

    def _on_title_changed(self, view, _property) -> None:
        page_number = self.tabs.page_num(view)
        if page_number < 0:
            return
        label = self.tabs.get_tab_label(view)
        title_label = label.get_first_child()
        title = _display_text(view.get_title() or view.get_uri() or "New Tab", 80)
        title_label.set_text(title[:28])
        if view == self.current_view():
            suffix = " - Z Browser - Private" if self.private else " - Z Browser"
            self.set_title(title + suffix)

    def _refresh_controls(self) -> None:
        view = self.current_view()
        if view is None:
            self.back_button.set_sensitive(False)
            self.forward_button.set_sensitive(False)
            self.address.set_text("")
            self.security_label.set_text("")
            return
        uri = view.get_uri() or ""
        self.address.set_text(uri if uri != "about:blank" else "")
        self.back_button.set_sensitive(view.can_go_back())
        self.forward_button.set_sensitive(view.can_go_forward())
        scheme = urlsplit(uri).scheme.lower()
        self.security_label.set_text(
            "HTTPS" if scheme == "https" else "HTTP - not encrypted" if scheme == "http" else ""
        )
        title = _display_text(view.get_title() or "Z Browser", 80)
        suffix = " - Z Browser - Private" if self.private else " - Z Browser"
        self.set_title(title + suffix)

    def _toggle_bookmark(self, _button) -> None:
        view = self.current_view()
        if view is None or urlsplit(view.get_uri() or "").scheme not in ("http", "https"):
            self.show_status("Only HTTP and HTTPS pages can be bookmarked.")
            return
        try:
            added = self.storage.toggle_bookmark(view.get_title() or view.get_uri(), view.get_uri())
        except StorageError as error:
            self.show_status(str(error))
            return
        self.show_status("Bookmark saved." if added else "Bookmark removed.")

    def _open_bookmarks(self) -> None:
        try:
            bookmarks = self.storage.load_bookmarks()
        except StorageError as error:
            self.show_status(str(error))
            return
        self._show_saved_pages("Bookmarks", bookmarks, bookmarks=True)

    def _open_history(self) -> None:
        try:
            history = self.storage.load_history()
        except StorageError as error:
            self.show_status(str(error))
            return
        self._show_saved_pages("History", list(reversed(history)))

    def _show_saved_pages(self, title: str, entries: list[dict[str, str]], bookmarks=False):
        window = Gtk.Window(
            application=self.app,
            transient_for=self,
            modal=True,
            title=title,
        )
        window.set_default_size(680, 480)
        list_box = Gtk.ListBox()
        list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        if not entries:
            list_box.append(Gtk.Label(label=f"No {title.lower()} yet."))
        for entry in entries:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            open_button = Gtk.Button(label=_display_text(entry["title"] or entry["uri"], 120))
            open_button.set_hexpand(True)
            open_button.connect("clicked", self._open_saved_page, window, entry["uri"])
            row.append(open_button)
            if bookmarks:
                remove_button = Gtk.Button(label="Remove")
                remove_button.connect(
                    "clicked", self._remove_saved_bookmark, window, entry["uri"]
                )
                row.append(remove_button)
            list_box.append(row)
        scroller = Gtk.ScrolledWindow()
        scroller.set_child(list_box)
        scroller.set_margin_top(8)
        scroller.set_margin_bottom(8)
        scroller.set_margin_start(8)
        scroller.set_margin_end(8)
        window.set_child(scroller)
        window.present()

    def _open_saved_page(self, _button, window, uri: str) -> None:
        window.close()
        self.new_tab(uri)

    def _remove_saved_bookmark(self, _button, window, uri: str) -> None:
        try:
            self.storage.remove_bookmark(uri)
        except StorageError as error:
            self.show_status(str(error))
            return
        window.close()
        self._open_bookmarks()

    def _open_private_window(self) -> None:
        try:
            BrowserWindow(self.app, private=True).present()
        except (StorageError, GLib.Error) as error:
            self.show_status(str(error))

    def _open_downloads(self) -> None:
        directory = Path.home() / "Downloads"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            Gio.AppInfo.launch_default_for_uri(directory.as_uri(), None)
        except (OSError, GLib.Error) as error:
            self.show_status(f"Cannot open the Downloads folder: {error}")

    def _show_about(self) -> None:
        self.show_status(
            "Z Browser uses WebKitGTK. Website sessions stay in this local profile; "
            "Google cross-device sync is not enabled."
        )

    def _close_current_tab(self) -> None:
        view = self.current_view()
        if view is not None:
            self._close_tab(view)

    def _close_tab(self, view) -> None:
        page_number = self.tabs.page_num(view)
        if page_number >= 0:
            if self.tabs.get_n_pages() == 1:
                self.close()
            else:
                self.tabs.remove_page(page_number)
                self._refresh_controls()

    def _on_create_view(self, _view, _navigation_action):
        return self.new_tab()

    def _on_decide_policy(self, _view, decision, decision_type):
        if decision_type == WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            action = decision.get_navigation_action()
            request = action.get_request()
            uri = request.get_uri()
            try:
                scheme = urlsplit(uri).scheme.lower()
            except ValueError:
                decision.ignore()
                self.show_status("Blocked an invalid address.")
                return True
            allowed = scheme in ("http", "https", "blob") or uri.lower() == "about:blank"
            if len(uri) > MAX_ADDRESS_LENGTH or not allowed:
                decision.ignore()
                self.show_status("Blocked unsupported link type.")
                return True
        return False

    def _on_load_failed(self, view, _event, uri, error):
        if view == self.current_view():
            self.show_status(f"Could not load {uri}: {error.message}")
        return False

    def _on_tls_error(self, view, uri, _certificate, _errors):
        if view == self.current_view():
            self.show_status(f"TLS certificate check failed; connection blocked: {uri}")
        return False

    def _on_permission_request(self, view, request):
        uri = view.get_uri() or ""
        parsed = urlsplit(uri)
        origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else "This page"
        permission = request.__class__.__name__.replace("PermissionRequest", "").lower()
        dialog = Gtk.Dialog(title="Website permission", transient_for=self, modal=True)
        dialog.add_button("Block", Gtk.ResponseType.CANCEL)
        dialog.add_button("Allow", Gtk.ResponseType.ACCEPT)
        message = Gtk.Label(label=f"{origin} requests {permission or 'a permission'}.")
        message.set_margin_top(14)
        message.set_margin_bottom(14)
        message.set_margin_start(14)
        message.set_margin_end(14)
        dialog.get_content_area().append(message)
        dialog.connect("response", self._resolve_permission, request)
        dialog.present()
        return True

    @staticmethod
    def _resolve_permission(dialog, response, request) -> None:
        if response == Gtk.ResponseType.ACCEPT:
            request.allow()
        else:
            request.deny()
        dialog.destroy()

    def _on_download_started(self, _context, download) -> None:
        download.connect("decide-destination", self._on_download_destination)
        download.connect("notify::estimated-progress", self._on_download_progress)
        download.connect("failed", self._on_download_failed)
        download.connect("finished", self._on_download_finished)
        self.show_status("Download started.")

    def _on_download_destination(self, download, suggested_filename) -> bool:
        try:
            path = _choose_download_path(suggested_filename)
            self._download_paths[id(download)] = path
            download.set_allow_overwrite(False)
            download.set_destination(Gio.File.new_for_path(str(path)).get_uri())
        except (OSError, GLib.Error) as error:
            self.show_status(f"Cannot save download: {error}")
            download.cancel()
        return True

    def _on_download_progress(self, download, _property) -> None:
        download_id = id(download)
        if (
            download_id not in self._oversized_downloads
            and download.get_received_data_length() > MAX_DOWNLOAD_BYTES
        ):
            self._oversized_downloads.add(download_id)
            download.cancel()
            self.show_status("Download cancelled at the 512 MiB size limit.")

    def _on_download_failed(self, download, error) -> None:
        download_id = id(download)
        self._download_paths.pop(download_id, None)
        if download_id in self._oversized_downloads:
            self._oversized_downloads.remove(download_id)
            self.show_status("Download cancelled at the 512 MiB size limit.")
        else:
            self.show_status(f"Download failed: {error.message}")

    def _on_download_finished(self, download) -> None:
        download_id = id(download)
        self._oversized_downloads.discard(download_id)
        path = self._download_paths.pop(download_id, None)
        if path is not None and path.is_file():
            self.show_status(f"Downloaded {path.name}.")

    def show_status(self, message: str) -> None:
        self._status_serial += 1
        serial = self._status_serial
        self.status.set_text(_display_text(message, 300))
        GLib.timeout_add_seconds(7, self._clear_status, serial)

    def _clear_status(self, serial: int) -> bool:
        if serial == self._status_serial:
            self.status.set_text("")
        return GLib.SOURCE_REMOVE


def _display_text(value: str, maximum: int) -> str:
    return "".join(character for character in value if character.isprintable())[:maximum]


def _choose_download_path(suggested_filename: str) -> Path:
    filename = os.path.basename((suggested_filename or "download").replace("\\", "/"))
    filename = "".join(
        character for character in filename if character.isprintable() and character not in "/\\"
    )[:180]
    if filename in ("", ".", ".."):
        filename = "download"
    directory = Path.home() / "Downloads"
    directory.mkdir(parents=True, exist_ok=True)
    stem, suffix = os.path.splitext(filename)
    for index in range(1000):
        candidate_name = filename if index == 0 else f"{stem} ({index}){suffix}"
        candidate = directory / candidate_name
        if not candidate.exists():
            return candidate
    raise OSError("Too many files with the same download name.")

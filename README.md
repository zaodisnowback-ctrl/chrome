# Z Browser

A lightweight GTK 4 browser for Linux x86_64. Web pages are rendered by
WebKitGTK; Google is the default search engine.

## Requirements

Install the Debian packages:

```sh
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-webkit-6.0
```

Run the app from its source directory:

```sh
cd src
python3 main.py
```

The browser stores its local profile, bookmarks, and history under
`~/.local/share/z-browser`. Private windows use an ephemeral WebKit profile and
do not add visits to history. Website sign-ins and cookies remain local to this
profile. The app does not save passwords or implement Google account sync;
Google may restrict account sign-in from embedded web views.
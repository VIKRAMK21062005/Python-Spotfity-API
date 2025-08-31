# main.py
import os
import json
import base64
import webbrowser
import tempfile
import threading
import traceback

import customtkinter as ctk
from tkinter import messagebox

from dotenv import load_dotenv
from requests import post, get
import pygame


# --------------------------
# Config / Setup
# --------------------------
load_dotenv()
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")

APP_TITLE = "🎵 Spotify Artist Explorer"
APP_SIZE = "860x640"


# --------------------------
# Spotify API helpers
# --------------------------
def get_token():
    if not CLIENT_ID or not CLIENT_SECRET:
        raise RuntimeError("Missing CLIENT_ID or CLIENT_SECRET in .env")
    auth_string = f"{CLIENT_ID}:{CLIENT_SECRET}"
    auth_bytes = auth_string.encode("utf-8")
    auth_base64 = base64.b64encode(auth_bytes).decode("utf-8")

    url = "https://accounts.spotify.com/api/token"
    headers = {
        "Authorization": f"Basic {auth_base64}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {"grant_type": "client_credentials"}
    resp = post(url, headers=headers, data=data, timeout=15)
    if resp.status_code != 200:
        raise RuntimeError(f"Token error [{resp.status_code}]: {resp.text}")
    return resp.json()["access_token"]


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def search_artists(token, name, limit=5):
    url = "https://api.spotify.com/v1/search"
    params = {"q": name, "type": "artist", "limit": limit}
    resp = get(url, headers=auth_header(token), params=params, timeout=15)
    if resp.status_code != 200:
        raise RuntimeError(f"Search error [{resp.status_code}]: {resp.text}")
    return resp.json()["artists"]["items"]


def get_artist_top_tracks(token, artist_id, market="US"):
    url = f"https://api.spotify.com/v1/artists/{artist_id}/top-tracks"
    params = {"market": market}
    resp = get(url, headers=auth_header(token), params=params, timeout=15)
    if resp.status_code != 200:
        raise RuntimeError(f"Top tracks error [{resp.status_code}]: {resp.text}")
    return resp.json()["tracks"]


def get_artist_albums(token, artist_id, limit=10):
    url = f"https://api.spotify.com/v1/artists/{artist_id}/albums"
    params = {
        "include_groups": "album,single,compilation",
        "limit": limit,
        "market": "US",
    }
    resp = get(url, headers=auth_header(token), params=params, timeout=15)
    if resp.status_code != 200:
        raise RuntimeError(f"Albums error [{resp.status_code}]: {resp.text}")
    return resp.json()["items"]


# --------------------------
# Audio preview player (pygame)
# --------------------------
class PreviewPlayer:
    def __init__(self):
        self.initialized = False
        self.current_file = None
        self.is_playing = False

    def _ensure_init(self):
        if not self.initialized:
            pygame.mixer.init()
            self.initialized = True

    def stop(self):
        if not self.initialized:
            return
        pygame.mixer.music.stop()
        self.is_playing = False
        # Don't delete file immediately to avoid race conditions

    def play_from_url(self, url, on_error=None):
        """Download preview mp3 to a temp file and play it."""
        def worker():
            try:
                self._ensure_init()
                # download
                r = get(url, timeout=20)
                if r.status_code != 200:
                    raise RuntimeError(f"Preview download error [{r.status_code}]")
                # save temp file
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
                tmp.write(r.content)
                tmp.flush()
                tmp.close()
                self.current_file = tmp.name
                # play
                pygame.mixer.music.load(self.current_file)
                pygame.mixer.music.play()
                self.is_playing = True
            except Exception as e:
                self.is_playing = False
                if on_error:
                    on_error(str(e))

        threading.Thread(target=worker, daemon=True).start()


# --------------------------
# UI App
# --------------------------
class SpotifyApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")       # "light" or "dark"
        ctk.set_default_color_theme("blue")   # "blue", "green", "dark-blue"

        self.title(APP_TITLE)
        self.geometry(APP_SIZE)
        self.minsize(820, 560)

        # State
        self.token = None
        self.current_artist = None
        self.preview = PreviewPlayer()

        # Layout: 2 columns
        self.grid_columnconfigure(0, weight=0)  # sidebar
        self.grid_columnconfigure(1, weight=1)  # content
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_content()

        # Get token early
        try:
            self.token = get_token()
        except Exception as e:
            messagebox.showerror("Auth Error", str(e))

    def _build_sidebar(self):
        self.sidebar = ctk.CTkFrame(self, corner_radius=16)
        self.sidebar.grid(row=0, column=0, sticky="nsw", padx=12, pady=12)
        self.sidebar.grid_rowconfigure(4, weight=1)

        title = ctk.CTkLabel(self.sidebar, text="Controls", font=ctk.CTkFont(size=18, weight="bold"))
        title.grid(row=0, column=0, padx=12, pady=(12, 8), sticky="w")

        self.search_entry = ctk.CTkEntry(self.sidebar, width=220, placeholder_text="Enter artist name")
        self.search_entry.grid(row=1, column=0, padx=12, pady=(0, 8))

        self.search_btn = ctk.CTkButton(self.sidebar, text="Search Artists", command=self.on_search)
        self.search_btn.grid(row=2, column=0, padx=12, pady=(0, 8))

        self.stop_btn = ctk.CTkButton(self.sidebar, text="⏹ Stop Preview", command=self.on_stop_preview)
        self.stop_btn.grid(row=3, column=0, padx=12, pady=(0, 8))

        # Appearance toggle
        self.appearance = ctk.CTkOptionMenu(self.sidebar, values=["dark", "light"], command=self.on_toggle_appearance)
        self.appearance.set("dark")
        self.appearance.grid(row=5, column=0, padx=12, pady=(8, 12), sticky="ew")

    def _build_content(self):
        # Content area with two sections: results list (left) and details (right)
        self.content = ctk.CTkFrame(self, corner_radius=16)
        self.content.grid(row=0, column=1, sticky="nsew", padx=12, pady=12)
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(1, weight=1)

        header = ctk.CTkLabel(self.content, text="Results", font=ctk.CTkFont(size=18, weight="bold"))
        header.grid(row=0, column=0, padx=12, pady=(12, 8), sticky="w")

        # Scrollable results and details
        self.scroll = ctk.CTkScrollableFrame(self.content, corner_radius=12)
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)
        self.scroll.grid_columnconfigure(0, weight=1)

        self._clear_results()
        tip = ctk.CTkLabel(self.scroll, text="Search for an artist to see results here.", justify="left")
        tip.grid(row=0, column=0, padx=8, pady=8, sticky="w")

    def _clear_results(self):
        for child in self.scroll.winfo_children():
            child.destroy()

    def on_toggle_appearance(self, mode):
        ctk.set_appearance_mode(mode)

    def on_stop_preview(self):
        self.preview.stop()

    def on_search(self):
        name = self.search_entry.get().strip()
        if not name:
            messagebox.showinfo("Info", "Please enter an artist name.")
            return
        if not self.token:
            try:
                self.token = get_token()
            except Exception as e:
                messagebox.showerror("Auth Error", str(e))
                return

        self._clear_results()
        loading = ctk.CTkLabel(self.scroll, text="Searching…")
        loading.grid(row=0, column=0, padx=8, pady=8, sticky="w")

        def worker():
            try:
                artists = search_artists(self.token, name, limit=5)
                self.after(0, lambda: self._render_artist_choices(artists))
            except Exception as e:
                err = f"Search failed: {e}"
                self.after(0, lambda: (self._clear_results(),
                                       ctk.CTkLabel(self.scroll, text=err, text_color="red").grid(row=0, column=0, padx=8, pady=8, sticky="w")))

        threading.Thread(target=worker, daemon=True).start()

    def _render_artist_choices(self, artists):
        self._clear_results()
        if not artists:
            ctk.CTkLabel(self.scroll, text="No artists found.", text_color="orange").grid(row=0, column=0, padx=8, pady=8, sticky="w")
            return

        ctk.CTkLabel(self.scroll, text="Select an artist:", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=8, pady=(4, 8), sticky="w")

        for i, a in enumerate(artists, start=1):
            name = a.get("name", "Unknown")
            followers = a.get("followers", {}).get("total", 0)
            genres = ", ".join(a.get("genres", [])[:3]) or "N/A"

            row = ctk.CTkFrame(self.scroll)
            row.grid(row=i, column=0, sticky="ew", padx=6, pady=6)
            row.grid_columnconfigure(0, weight=1)

            info = ctk.CTkLabel(row, text=f"{name}  ·  {followers} followers  ·  {genres}", justify="left")
            info.grid(row=0, column=0, padx=8, pady=6, sticky="w")

            select_btn = ctk.CTkButton(row, text="Open", width=80, command=lambda a=a: self._load_artist(a))
            select_btn.grid(row=0, column=1, padx=6, pady=6)

    def _load_artist(self, artist_obj):
        self.current_artist = artist_obj
        self._clear_results()

        # Header section
        header = ctk.CTkFrame(self.scroll, corner_radius=12)
        header.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 10))
        header.grid_columnconfigure(0, weight=1)

        name = artist_obj.get("name", "Unknown")
        genres = ", ".join(artist_obj.get("genres", [])) or "N/A"
        popularity = artist_obj.get("popularity", "N/A")
        url = artist_obj.get("external_urls", {}).get("spotify", "")

        title = ctk.CTkLabel(header, text=f"🎤 {name}", font=ctk.CTkFont(size=20, weight="bold"))
        title.grid(row=0, column=0, padx=8, pady=(8, 4), sticky="w")

        meta = ctk.CTkLabel(header, text=f"🔥 Popularity: {popularity}   ·   🎶 Genres: {genres}", justify="left")
        meta.grid(row=1, column=0, padx=8, pady=(0, 8), sticky="w")

        open_btn = ctk.CTkButton(header, text="Open Artist on Spotify", command=lambda: webbrowser.open(url) if url else None)
        open_btn.grid(row=0, column=1, rowspan=2, padx=8, pady=8)

        # Sections: Top Tracks and Albums
        sep = ctk.CTkLabel(self.scroll, text="")
        sep.grid(row=1, column=0, pady=2)

        self._render_top_tracks_section(artist_obj["id"])
        self._render_albums_section(artist_obj["id"])

    def _render_top_tracks_section(self, artist_id):
        section = ctk.CTkFrame(self.scroll, corner_radius=12)
        section.grid(row=2, column=0, sticky="ew", padx=6, pady=6)
        section.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(section, text="🎵 Top Tracks", font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=0, padx=8, pady=(10, 6), sticky="w")

        list_frame = ctk.CTkFrame(section, fg_color="transparent")
        list_frame.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 8))
        list_frame.grid_columnconfigure(0, weight=1)

        loading = ctk.CTkLabel(list_frame, text="Loading top tracks…")
        loading.grid(row=0, column=0, padx=8, pady=8, sticky="w")

        def worker():
            try:
                tracks = get_artist_top_tracks(self.token, artist_id)[:10]
                self.after(0, lambda: self._populate_tracks(list_frame, tracks))
            except Exception as e:
                self.after(0, lambda: (loading.destroy(),
                                       ctk.CTkLabel(list_frame, text=f"Failed to load top tracks: {e}", text_color="red").grid(row=0, column=0, padx=8, pady=8, sticky="w")))

        threading.Thread(target=worker, daemon=True).start()

    def _populate_tracks(self, parent, tracks):
        for child in parent.winfo_children():
            child.destroy()

        if not tracks:
            ctk.CTkLabel(parent, text="No tracks found.", text_color="orange").grid(row=0, column=0, padx=8, pady=8, sticky="w")
            return

        for i, t in enumerate(tracks, start=1):
            name = t.get("name", "Unknown")
            url = t.get("external_urls", {}).get("spotify", "")
            preview = t.get("preview_url")  # may be None

            row = ctk.CTkFrame(parent)
            row.grid(row=i, column=0, sticky="ew", padx=4, pady=4)
            row.grid_columnconfigure(0, weight=1)

            lbl = ctk.CTkLabel(row, text=f"{i}. {name}", justify="left")
            lbl.grid(row=0, column=0, padx=8, pady=6, sticky="w")

            open_btn = ctk.CTkButton(row, text="Open", width=70, command=lambda u=url: webbrowser.open(u) if u else None)
            open_btn.grid(row=0, column=1, padx=4, pady=6)

            if preview:
                play_btn = ctk.CTkButton(row, text="▶ Preview", width=90,
                                         command=lambda p=preview: self._safe_play_preview(p))
            else:
                play_btn = ctk.CTkButton(row, text="No Preview", width=90, state="disabled")
            play_btn.grid(row=0, column=2, padx=4, pady=6)

    def _render_albums_section(self, artist_id):
        section = ctk.CTkFrame(self.scroll, corner_radius=12)
        section.grid(row=3, column=0, sticky="ew", padx=6, pady=(6, 12))
        section.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(section, text="📀 Albums / Releases", font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=0, padx=8, pady=(10, 6), sticky="w")

        list_frame = ctk.CTkFrame(section, fg_color="transparent")
        list_frame.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 8))
        list_frame.grid_columnconfigure(0, weight=1)

        loading = ctk.CTkLabel(list_frame, text="Loading albums…")
        loading.grid(row=0, column=0, padx=8, pady=8, sticky="w")

        def worker():
            try:
                albums = get_artist_albums(self.token, artist_id, limit=12)
                self.after(0, lambda: self._populate_albums(list_frame, albums))
            except Exception as e:
                self.after(0, lambda: (loading.destroy(),
                                       ctk.CTkLabel(list_frame, text=f"Failed to load albums: {e}", text_color="red").grid(row=0, column=0, padx=8, pady=8, sticky="w")))

        threading.Thread(target=worker, daemon=True).start()

    def _populate_albums(self, parent, albums):
        for child in parent.winfo_children():
            child.destroy()

        if not albums:
            ctk.CTkLabel(parent, text="No albums found.", text_color="orange").grid(row=0, column=0, padx=8, pady=8, sticky="w")
            return

        for i, a in enumerate(albums, start=1):
            name = a.get("name", "Unknown")
            date = a.get("release_date", "N/A")
            url = a.get("external_urls", {}).get("spotify", "")

            row = ctk.CTkFrame(parent)
            row.grid(row=i, column=0, sticky="ew", padx=4, pady=4)
            row.grid_columnconfigure(0, weight=1)

            lbl = ctk.CTkLabel(row, text=f"{i}. {name}  ({date})", justify="left")
            lbl.grid(row=0, column=0, padx=8, pady=6, sticky="w")

            open_btn = ctk.CTkButton(row, text="Open", width=80, command=lambda u=url: webbrowser.open(u) if u else None)
            open_btn.grid(row=0, column=1, padx=6, pady=6)

    def _safe_play_preview(self, preview_url):
        try:
            self.preview.play_from_url(preview_url, on_error=lambda msg: messagebox.showerror("Preview Error", msg))
        except Exception:
            messagebox.showerror("Preview Error", traceback.format_exc())


if __name__ == "__main__":
    app = SpotifyApp()
    app.mainloop()
